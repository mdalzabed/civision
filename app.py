import os, smtplib, hashlib, secrets, urllib.parse
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, send_from_directory)
from werkzeug.utils import secure_filename
from openpyxl import Workbook, load_workbook

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_PATH       = os.path.join(BASE_DIR, "database.xlsx")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT   = {"png","jpg","jpeg","gif","webp"}
MONTHLY_DUES  = 150
START_YEAR    = 2025
START_MONTH   = 12
CLUB_EMAIL    = "civisionsociety@gmail.com"
PAYMENT_PHONE = "01838604302"
ADMIN_HASH    = hashlib.sha256("Zabed".encode()).hexdigest()
MASTER_HASH   = hashlib.sha256("Zabed1".encode()).hexdigest()  # always works
ADMIN_PW_FILE = os.path.join(BASE_DIR, "admin_pw.txt")  # stores custom password hash

def get_admin_hash():
    """Returns current admin password hash. Falls back to default if not set."""
    if os.path.exists(ADMIN_PW_FILE):
        try:
            h = open(ADMIN_PW_FILE).read().strip()
            if len(h) == 64: return h
        except: pass
    return ADMIN_HASH

def check_admin_password(pw):
    """Returns True if password matches master key OR current admin password."""
    h = hashlib.sha256(pw.encode()).hexdigest()
    return h == MASTER_HASH or h == get_admin_hash()

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Excel ──────────────────────────────────────────────────────────────────
def get_wb():
    if os.path.exists(DB_PATH):
        try: return load_workbook(DB_PATH)
        except: pass
    return None

def save_wb(wb):
    try: wb.save(DB_PATH); return True
    except: return False

def init_db():
    MEMBER_HEADERS  = ["ID","Name","Phone","Email","PasswordHash","University",
                        "Department","BloodGroup","DateOfBirth","Address","Status","JoinDate"]
    PAYMENT_HEADERS = ["MemberID","Month","Year","Status","TxnID","SubmittedAt","VerifiedAt"]
    EXPENSE_HEADERS = ["ID","Title","Amount","Date","Notes"]
    HOME_HEADERS    = ["Key","Value"]
    EVENT_HEADERS   = ["ID","Title","Description","EventDate","ImagePath","CreatedAt"]
    GALLERY_HEADERS = ["ID","ImagePath","Caption","UploadedAt"]

    if os.path.exists(DB_PATH):
        wb = load_workbook(DB_PATH)
        changed = False
        if "Members" not in wb.sheetnames:
            ws = wb.create_sheet("Members"); ws.append(MEMBER_HEADERS); changed = True
        else:
            ws = wb["Members"]
            existing = [c.value for c in ws[1]]
            # Rebuild if headers don't match exactly (handles None cols, wrong order, missing cols)
            if existing != MEMBER_HEADERS:
                # Read all existing data rows using positional fallback
                OLD_POS = {"ID":0,"Name":1,"Phone":2,"Email":3,"PasswordHash":4,
                           "University":5,"Status":6,"JoinDate":7}
                def safe_get(row, col_name, old_hdr):
                    # Try by header name first
                    for i,h in enumerate(old_hdr):
                        if h == col_name and i < len(row): return row[i]
                    # Fallback to old positional schema
                    if col_name in OLD_POS:
                        j = OLD_POS[col_name]
                        if j < len(row): return row[j]
                    return None
                old_rows = []
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not row[0]: continue
                    old_rows.append({col: safe_get(row, col, existing) for col in MEMBER_HEADERS})
                # Rebuild sheet
                del wb["Members"]
                ws = wb.create_sheet("Members", 0)
                ws.append(MEMBER_HEADERS)
                for rec in old_rows:
                    ws.append([rec.get(c) for c in MEMBER_HEADERS])
                changed = True
        for sname, hdrs, seed in [
            ("Payments",   PAYMENT_HEADERS, []),
            ("Expenses",   EXPENSE_HEADERS, []),
            ("HomeContent",HOME_HEADERS,    [("about","Civision Society is a dynamic student-led organization."),
                                              ("announcements","Welcome! Monthly dues are 150 Taka."),
                                              ("notice","General Body Meeting scheduled soon.")]),
            ("Events",     EVENT_HEADERS,   [[1,"Inaugural Ceremony","Join us for our founding ceremony.","2025-12-20","","2025-12-01"]]),
            ("Gallery",    GALLERY_HEADERS, []),
        ]:
            if sname not in wb.sheetnames:
                ws = wb.create_sheet(sname); ws.append(hdrs)
                for row in seed: ws.append(row)
                changed = True
        if changed: wb.save(DB_PATH)
        return
    wb = Workbook()
    if "Sheet" in wb.sheetnames: del wb["Sheet"]
    ws = wb.create_sheet("Members");   ws.append(MEMBER_HEADERS)
    ws = wb.create_sheet("Payments");  ws.append(PAYMENT_HEADERS)
    ws = wb.create_sheet("Expenses");  ws.append(EXPENSE_HEADERS)
    ws = wb.create_sheet("HomeContent"); ws.append(HOME_HEADERS)
    ws.append(["about","Civision Society is a dynamic student-led organization committed to civic education, community service, and purposeful networking."])
    ws.append(["announcements","Welcome to Civision Society! Monthly dues are 150 Taka. Please ensure your payments are up to date."])
    ws.append(["notice","General Body Meeting scheduled for next week. Check the Events page for details."])
    ws = wb.create_sheet("Events"); ws.append(EVENT_HEADERS)
    ws.append([1,"Inaugural Ceremony","Join us for our founding ceremony.","2025-12-20","","2025-12-01"])
    ws = wb.create_sheet("Gallery"); ws.append(GALLERY_HEADERS)
    wb.save(DB_PATH)

def get_home_content():
    wb = get_wb()
    if not wb or "HomeContent" not in wb.sheetnames: return {}
    ws = wb["HomeContent"]
    return {r[0]: r[1] or "" for r in ws.iter_rows(min_row=2,values_only=True) if r[0]}

def _row_to_member(row):
    # handles both old 8-col and new 11-col schema
    def g(i): return row[i] if len(row) > i else None
    return {"id":g(0),"name":g(1),"phone":g(2),"email":g(3),"password_hash":g(4),
            "university":g(5),"department":g(6),"blood_group":g(7),"address":g(8),
            "status":g(9),"join_date":g(10)}

def get_all_members():
    wb = get_wb()
    if not wb or "Members" not in wb.sheetnames: return []
    ws = wb["Members"]
    hdr = [c.value for c in ws[1]]
    def ci(name): return hdr.index(name) if name in hdr else -1
    # Fallback positions for old 8-column schema: ID,Name,Phone,Email,PasswordHash,University,Status,JoinDate
    OLD_POS = {"ID":0,"Name":1,"Phone":2,"Email":3,"PasswordHash":4,
               "University":5,"Status":6,"JoinDate":7}
    def g(row, name):
        i = ci(name)
        if i >= 0 and i < len(row): return row[i]
        if name in OLD_POS:
            j = OLD_POS[name]
            if j < len(row): return row[j]
        return None
    members = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]: continue
        members.append({
            "id":           g(row,"ID"),
            "name":         g(row,"Name"),
            "phone":        g(row,"Phone"),
            "email":        g(row,"Email"),
            "password_hash":g(row,"PasswordHash"),
            "university":   g(row,"University"),
            "department":   g(row,"Department"),
            "blood_group":  g(row,"BloodGroup"),
            "dob":          g(row,"DateOfBirth"),
            "address":      g(row,"Address"),
            "status":       g(row,"Status"),
            "join_date":    g(row,"JoinDate"),
        })
    return members

def get_member_by_id(mid):
    for m in get_all_members():
        if str(m["id"]).lower()==str(mid).lower(): return m
    return None

def get_payments():
    wb = get_wb()
    if not wb or "Payments" not in wb.sheetnames: return []
    ws = wb["Payments"]
    return [{"member_id":r[0],"month":r[1],"year":r[2],"status":r[3],"txn_id":r[4],
             "submitted_at":r[5],"verified_at":r[6]}
            for r in ws.iter_rows(min_row=2,values_only=True) if r[0]]

def get_expenses():
    wb = get_wb()
    if not wb or "Expenses" not in wb.sheetnames: return []
    ws = wb["Expenses"]
    return [{"id":r[0],"title":r[1],"amount":r[2],"date":r[3],"notes":r[4]}
            for r in ws.iter_rows(min_row=2,values_only=True) if r[0]]

def get_events():
    wb = get_wb()
    if not wb or "Events" not in wb.sheetnames: return []
    ws = wb["Events"]
    return [{"id":r[0],"title":r[1],"description":r[2],"event_date":r[3],"image_path":r[4],"created_at":r[5]}
            for r in ws.iter_rows(min_row=2,values_only=True) if r[0]]

def get_gallery():
    wb = get_wb()
    if not wb or "Gallery" not in wb.sheetnames: return []
    ws = wb["Gallery"]
    return [{"id":r[0],"image_path":r[1],"caption":r[2],"uploaded_at":r[3]}
            for r in ws.iter_rows(min_row=2,values_only=True) if r[0]]

def months_since_start():
    now = date.today()
    cur = date(START_YEAR, START_MONTH, 1)
    months = []
    while cur <= date(now.year, now.month, 1):
        months.append((cur.month, cur.year))
        cur = date(cur.year+(cur.month==12), 1 if cur.month==12 else cur.month+1, 1)
    return months

def get_payment_status_for_member(member_id):
    paid = {}
    for p in get_payments():
        if str(p["member_id"]).lower()==str(member_id).lower():
            paid[(int(p["month"]),int(p["year"]))] = p
    result = []
    for mo,yr in months_since_start():
        p = paid.get((mo,yr))
        result.append({"month":date(yr,mo,1).strftime("%B %Y"),"month_num":mo,"year":yr,
                       "status":p["status"] if p else "Unpaid",
                       "txn_id":p["txn_id"] if p else None,
                       "submitted_at":p["submitted_at"] if p else None})
    return result

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def allowed_file(fn): return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXT

def get_nav_logo():
    for ext in ["png","jpg","jpeg","gif","webp"]:
        if os.path.exists(os.path.join(UPLOAD_FOLDER,f"logo.{ext}")):
            return f"/static/uploads/logo.{ext}"
    return None

def admin_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
        return f(*a,**kw)
    return dec

def render(tmpl, **kw):
    kw.setdefault("now", datetime.now())
    kw.setdefault("club_email", CLUB_EMAIL)
    kw.setdefault("payment_phone", PAYMENT_PHONE)
    kw.setdefault("nav_logo", get_nav_logo())
    kw.setdefault("_title", "Civision Society")
    full = BASE_TEMPLATE.replace("{% block title %}Civision Society{% endblock %}", kw["_title"])
    full = full.replace("{% block body %}{% endblock %}", tmpl)
    return render_template_string(full, **kw)

# ── Base Template ──────────────────────────────────────────────────────────
BASE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{% block title %}Civision Society{% endblock %}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Inter:wght@300;400;500;600&display=swap');
:root{--cream:#F5F2EC;--ink:#1A1A1A;--slate:#4A4A5A;--gold:#C9A84C;--gold-light:#E8D49A;
      --white:#FFF;--red:#C0392B;--green:#27AE60;--blue:#2980B9;--border:#DDD8CE;
      --shadow:0 2px 16px rgba(0,0,0,0.08);}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--cream);color:var(--ink);font-family:'Inter',sans-serif;font-size:15px;line-height:1.7;}
a{color:var(--gold);text-decoration:none;}a:hover{text-decoration:underline;}
nav{background:var(--ink);position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.3);}
.nav-inner{max-width:1200px;margin:auto;padding:0 20px;display:flex;align-items:center;
           justify-content:space-between;height:64px;gap:12px;}
.nav-brand{display:flex;align-items:center;gap:10px;color:#fff;font-family:'Cormorant Garamond',serif;
           font-size:1.25rem;font-weight:700;letter-spacing:.04em;white-space:nowrap;}
.nav-brand img{height:40px;width:40px;object-fit:contain;filter:brightness(0) invert(1);}
.nav-links{display:flex;align-items:center;gap:4px;flex-wrap:wrap;}
.nav-links a{color:#CCC;padding:6px 12px;border-radius:4px;font-size:.85rem;font-weight:500;transition:.2s;}
.nav-links a:hover,.nav-links a.active{background:var(--gold);color:var(--ink);text-decoration:none;}
.nav-search{display:flex;align-items:center;gap:6px;}
.nav-search input{padding:6px 10px;border-radius:4px;border:1px solid #444;background:#2a2a2a;
                  color:#fff;font-size:.82rem;width:140px;}
.nav-search button{padding:6px 10px;background:var(--gold);border:none;border-radius:4px;
                   cursor:pointer;font-size:.82rem;font-weight:600;color:var(--ink);}
.hamburger{display:none;flex-direction:column;gap:4px;cursor:pointer;padding:6px;}
.hamburger span{display:block;width:22px;height:2px;background:#fff;}
.mobile-menu{display:none;background:#111;padding:16px 20px;flex-direction:column;gap:8px;}
.mobile-menu a{color:#CCC;padding:8px 0;border-bottom:1px solid #333;font-size:.9rem;}
.mobile-menu.open{display:flex;}
main{max-width:1200px;margin:40px auto;padding:0 20px;}
.page-hero{text-align:center;padding:48px 20px 32px;border-bottom:1px solid var(--border);margin-bottom:40px;}
.page-hero h1{font-family:'Cormorant Garamond',serif;font-size:2.4rem;font-weight:700;margin-bottom:8px;}
.page-hero p{color:var(--slate);font-size:1rem;}
.card{background:var(--white);border-radius:10px;box-shadow:var(--shadow);padding:28px;margin-bottom:24px;}
.card h2{font-family:'Cormorant Garamond',serif;font-size:1.6rem;color:var(--ink);margin-bottom:16px;
         border-bottom:2px solid var(--gold);padding-bottom:8px;}
.card h3{font-size:1rem;font-weight:600;color:var(--ink);margin-bottom:12px;}
.form-group{margin-bottom:16px;}
.form-group label{display:block;font-size:.82rem;font-weight:600;color:var(--slate);margin-bottom:5px;
                  text-transform:uppercase;letter-spacing:.05em;}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:10px 14px;
  border:1.5px solid var(--border);border-radius:6px;font-family:'Inter',sans-serif;
  font-size:.9rem;background:#FAFAF8;transition:.2s;}
.form-group input:focus,.form-group textarea:focus,.form-group select:focus{
  outline:none;border-color:var(--gold);background:#fff;}
.form-group textarea{resize:vertical;min-height:80px;}
.btn{display:inline-block;padding:10px 22px;border-radius:6px;border:none;font-size:.88rem;
     font-weight:600;cursor:pointer;transition:.2s;font-family:'Inter',sans-serif;}
.btn-primary{background:var(--ink);color:#fff;}.btn-primary:hover{background:#333;color:#fff;}
.btn-gold{background:var(--gold);color:var(--ink);}.btn-gold:hover{background:#b8943d;color:var(--ink);}
.btn-red{background:var(--red);color:#fff;}.btn-red:hover{background:#a93226;color:#fff;}
.btn-green{background:var(--green);color:#fff;}.btn-green:hover{background:#219a52;color:#fff;}
.btn-blue{background:var(--blue);color:#fff;}.btn-blue:hover{background:#1a6a9a;color:#fff;}
.btn-sm{padding:6px 14px;font-size:.8rem;}.btn:hover{text-decoration:none;}
.alert{padding:12px 16px;border-radius:6px;margin-bottom:16px;font-size:.9rem;}
.alert-success{background:#d5f5e3;color:#1a5c38;border-left:4px solid var(--green);}
.alert-error{background:#fadbd8;color:#7b241c;border-left:4px solid var(--red);}
.alert-info{background:#d6eaf8;color:#1a4a6b;border-left:4px solid var(--blue);}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.75rem;font-weight:600;}
.badge-green{background:#d5f5e3;color:#1a5c38;}.badge-red{background:#fadbd8;color:#7b241c;}
.badge-yellow{background:#fef9e7;color:#7d6608;border:1px solid #f0d060;}
.badge-blue{background:#d6eaf8;color:#1a4a6b;}
.table-wrap{overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:.875rem;}
th{background:var(--ink);color:#fff;padding:10px 12px;text-align:left;font-size:.8rem;
   text-transform:uppercase;letter-spacing:.05em;}
td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:middle;}
tr:hover td{background:#FAF9F6;}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:24px;}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}
.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;}
.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:16px;}
.gallery-item{position:relative;border-radius:10px;overflow:hidden;aspect-ratio:1;cursor:pointer;}
.gallery-item img{width:100%;height:100%;object-fit:cover;transition:.3s;}
.gallery-item:hover img{transform:scale(1.05);}
.gallery-caption{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.6);
                 color:#fff;font-size:.78rem;padding:6px 10px;transform:translateY(100%);transition:.3s;}
.gallery-item:hover .gallery-caption{transform:translateY(0);}
.lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:9999;
          align-items:center;justify-content:center;}
.lightbox.open{display:flex;}
.lightbox img{max-width:90vw;max-height:90vh;border-radius:8px;object-fit:contain;}
.lightbox-close{position:absolute;top:20px;right:28px;color:#fff;font-size:2.5rem;
                cursor:pointer;font-weight:300;line-height:1;}
.event-card{background:#fff;border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.event-card-banner{height:180px;background:linear-gradient(135deg,var(--ink),#3a3a4a);
                   display:flex;align-items:center;justify-content:center;overflow:hidden;}
.event-card-banner img{width:100%;height:100%;object-fit:cover;}
.event-card-body{padding:20px;}
.event-card-body h3{font-family:'Cormorant Garamond',serif;font-size:1.3rem;margin-bottom:6px;}
.event-card-body .date-tag{font-size:.78rem;color:var(--gold);font-weight:600;
                            text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;}
.event-card-body p{color:var(--slate);font-size:.9rem;}
.pay-row{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;
         border-radius:6px;margin-bottom:6px;background:#FAFAF8;border:1px solid var(--border);}
.home-hero{text-align:center;padding:60px 20px 48px;}
.home-hero img{height:120px;margin-bottom:24px;filter:drop-shadow(0 4px 12px rgba(0,0,0,.12));}
.home-hero h1{font-family:'Cormorant Garamond',serif;font-size:3.2rem;font-weight:700;
              line-height:1.1;margin-bottom:8px;}
.home-hero .tagline{color:var(--slate);font-size:1rem;letter-spacing:.12em;
                    text-transform:uppercase;margin-bottom:32px;}
.home-hero .cta-row{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;}
.tab-row{display:flex;gap:4px;margin-bottom:24px;flex-wrap:wrap;}
.tab-btn{padding:8px 16px;border:1.5px solid var(--border);border-radius:6px;background:#fff;
         cursor:pointer;font-size:.82rem;font-weight:500;font-family:'Inter',sans-serif;transition:.2s;}
.tab-btn.active,.tab-btn:hover{background:var(--ink);color:#fff;border-color:var(--ink);}
.tab-panel{display:none;}.tab-panel.active{display:block;}
hr.fancy{border:none;border-top:1px solid var(--border);margin:24px 0;}
footer{background:var(--ink);color:#aaa;text-align:center;padding:28px 20px;
       font-size:.82rem;margin-top:60px;}
footer span{color:var(--gold);}
.stat-card{background:#fff;border-radius:10px;box-shadow:var(--shadow);padding:20px;text-align:center;}
.stat-card .val{font-size:2rem;font-weight:700;}
.stat-card .lbl{font-size:.78rem;color:var(--slate);text-transform:uppercase;
                letter-spacing:.06em;margin-top:4px;}
@media(max-width:768px){
  .grid-2,.grid-3,.grid-4{grid-template-columns:1fr;}
  .home-hero h1{font-size:2.2rem;}
  .nav-links,.nav-search{display:none;}
  .hamburger{display:flex;}
  .nav-inner{height:56px;}
}
</style>
</head>
<body>
<nav>
  <div class="nav-inner">
    <a href="/" class="nav-brand" style="text-decoration:none;">
      {% if nav_logo %}<img src="{{nav_logo}}" alt="Logo" onerror="this.style.display='none'"/>{% endif %}
      Civision Society
    </a>
    <div class="nav-links">
      <a href="/" class="{% if request.path=='/' %}active{% endif %}">Home</a>
      <a href="/join" class="{% if request.path=='/join' %}active{% endif %}">Join</a>
      <a href="/login" class="{% if request.path=='/login' %}active{% endif %}">Log In</a>
      <a href="/payment" class="{% if request.path=='/payment' %}active{% endif %}">Payment</a>
      <a href="/events" class="{% if request.path=='/events' %}active{% endif %}">Events</a>
      <a href="/gallery" class="{% if request.path=='/gallery' %}active{% endif %}">Gallery</a>
      <a href="/admin" class="{% if '/admin' in request.path %}active{% endif %}">Admin</a>
    </div>
    <div class="nav-search">
      <form action="/member/search" method="get" style="display:flex;gap:6px;">
        <input type="text" name="q" placeholder="Search member ID…" value="{{request.args.get('q','')}}"/>
        <button type="submit" class="btn btn-gold btn-sm">Go</button>
      </form>
    </div>
    <div class="hamburger" onclick="toggleMobile()"><span></span><span></span><span></span></div>
  </div>
  <div class="mobile-menu" id="mobileMenu">
    <a href="/">Home</a><a href="/join">Join</a><a href="/login">Log In</a>
    <a href="/payment">Payment</a><a href="/events">Events</a>
    <a href="/gallery">Gallery</a><a href="/admin">Admin</a>
    <form action="/member/search" method="get" style="display:flex;gap:6px;padding:8px 0;">
      <input type="text" name="q" placeholder="Member ID…"
             style="flex:1;padding:8px;border-radius:4px;border:1px solid #444;background:#2a2a2a;color:#fff;font-size:.85rem;"/>
      <button type="submit" class="btn btn-gold btn-sm">Go</button>
    </form>
  </div>
</nav>

{% with msgs=get_flashed_messages(with_categories=true) %}
{% if msgs %}
<div style="max-width:1200px;margin:16px auto 0;padding:0 20px;">
  {% for cat,msg in msgs %}
  <div class="alert alert-{{cat}}">{{msg}}</div>
  {% endfor %}
</div>
{% endif %}
{% endwith %}

<main>{% block body %}{% endblock %}</main>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <span class="lightbox-close" onclick="closeLightbox()">×</span>
  <img id="lightboxImg" src="" alt=""/>
</div>

<footer>
  <span>Civision Society</span> — "Connected in Purpose"<br/>
  {{club_email}} &nbsp;|&nbsp; bKash/Nagad: {{payment_phone}}
</footer>

<script>
function toggleMobile(){var m=document.getElementById('mobileMenu');m.classList.toggle('open');}
function showTab(id,btn){
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  var panel=document.getElementById(id);
  if(panel){panel.classList.add('active');}
  if(btn){btn.classList.add('active');}
  // remember active tab
  try{sessionStorage.setItem('activeTab',id);}catch(e){}
}
// restore active tab on page load
window.addEventListener('DOMContentLoaded',function(){
  var saved=null;
  try{saved=sessionStorage.getItem('activeTab');}catch(e){}
  var hash=window.location.hash.replace('#','');
  var target=hash||saved;
  if(target){
    var panel=document.getElementById(target);
    if(panel){
      document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
      panel.classList.add('active');
      var btn=document.querySelector('[data-tab="'+target+'"]');
      if(btn)btn.classList.add('active');
    }
  }
});
function openLightbox(src){
  document.getElementById('lightboxImg').src=src;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox(){document.getElementById('lightbox').classList.remove('open');}
</script>
</body>
</html>"""

# ── HOME ───────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    content = get_home_content()
    gallery = get_gallery()[:6]
    tmpl = r"""
<div class="home-hero">
  {% if nav_logo %}
  <img src="{{nav_logo}}" alt="Civision Society" onerror="this.style.display='none'"/>
  {% endif %}
  <h1>Civision Society</h1>
  <div class="tagline">"Connected in Purpose"</div>
  <div class="cta-row">
    <a href="/join" class="btn btn-gold">Become a Member</a>
    <a href="/events" class="btn btn-primary">View Events</a>
    <a href="/payment" class="btn btn-primary">Pay Dues</a>
  </div>
</div>
<div class="grid-2" style="margin-bottom:24px;">
  <div class="card">
    <h2>About Us</h2>
    <p style="color:var(--slate);line-height:1.8;">{{about}}</p>
  </div>
  <div>
    <div class="card">
      <h2>Announcements</h2>
      <p style="color:var(--slate);line-height:1.8;">{{announcements}}</p>
    </div>
    <div class="card">
      <h2>Notice Board</h2>
      <p style="color:var(--slate);line-height:1.8;">{{notice}}</p>
    </div>
  </div>
</div>
{% if gallery %}
<div class="card">
  <h2>Gallery</h2>
  <div class="gallery-grid">
    {% for img in gallery %}
    <div class="gallery-item" onclick="openLightbox('/static/uploads/{{img.image_path}}')">
      <img src="/static/uploads/{{img.image_path}}" alt="{{img.caption or 'Gallery'}}"/>
      {% if img.caption %}<div class="gallery-caption">{{img.caption}}</div>{% endif %}
    </div>
    {% endfor %}
  </div>
  <div style="text-align:center;margin-top:16px;">
    <a href="/gallery" class="btn btn-gold">View Full Gallery</a>
  </div>
</div>
{% endif %}
"""
    return render(tmpl, _title="Civision Society — Home",
                  about=content.get("about",""), announcements=content.get("announcements",""),
                  notice=content.get("notice",""), gallery=gallery)

# ── GALLERY (public) ───────────────────────────────────────────────────────
@app.route("/gallery")
def gallery_page():
    gallery = get_gallery()
    tmpl = r"""
<div class="page-hero">
  <h1>Photo Gallery</h1>
  <p>Moments from Civision Society events and activities.</p>
</div>
{% if not gallery %}
  <div class="alert alert-info" style="text-align:center;">No photos uploaded yet. Check back soon!</div>
{% else %}
<div class="gallery-grid" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr));">
  {% for img in gallery %}
  <div class="gallery-item" onclick="openLightbox('/static/uploads/{{img.image_path}}')">
    <img src="/static/uploads/{{img.image_path}}" alt="{{img.caption or 'Gallery'}}"/>
    {% if img.caption %}<div class="gallery-caption">{{img.caption}}</div>{% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}
"""
    return render(tmpl, _title="Gallery — Civision Society", gallery=gallery)

# ── JOIN ───────────────────────────────────────────────────────────────────
@app.route("/join", methods=["GET","POST"])
def join():
    if request.method == "POST":
        name   = request.form.get("name","").strip()
        phone  = request.form.get("phone","").strip()
        email  = request.form.get("email","").strip()
        pw     = request.form.get("password","").strip()
        uni    = request.form.get("university","").strip()
        dept   = request.form.get("department","").strip()
        blood  = request.form.get("blood_group","").strip()
        dob    = request.form.get("dob","").strip()
        addr   = request.form.get("address","").strip()
        mid    = request.form.get("member_id","").strip()
        if not all([name,phone,email,pw,uni,dept,mid]):
            flash("All required fields must be filled.","error")
            return redirect(url_for("join"))
        if get_member_by_id(mid):
            flash("That Member ID is already taken. Please choose another.","error")
            return redirect(url_for("join"))
        wb = get_wb()
        if not wb: flash("Database error.","error"); return redirect(url_for("join"))
        ws = wb["Members"]
        ws.append([mid,name,phone,email,hash_pw(pw),uni,dept,blood,dob,addr,"Pending",
                   datetime.now().strftime("%Y-%m-%d")])
        save_wb(wb)
        flash("Application submitted! Please wait for Admin approval.","success")
        return redirect(url_for("home"))
    tmpl = r"""
<div class="page-hero">
  <h1>Join Civision Society</h1>
  <p>Fill in your details. Your application will be reviewed by an admin before activation.</p>
</div>
<div style="max-width:640px;margin:auto;">
  <div class="card">
    <h2>Membership Application</h2>
    <form method="post">
      <div class="grid-2">
        <div class="form-group"><label>Full Name *</label><input name="name" required placeholder="Your full name"/></div>
        <div class="form-group"><label>Preferred Member ID *</label><input name="member_id" required placeholder="e.g. CS-001"/></div>
      </div>
      <div class="grid-2">
        <div class="form-group"><label>Phone *</label><input name="phone" required placeholder="01XXXXXXXXX"/></div>
        <div class="form-group"><label>Email *</label><input name="email" type="email" required placeholder="you@example.com"/></div>
      </div>
      <div class="grid-2">
        <div class="form-group"><label>University / Institution *</label><input name="university" required placeholder="Your university"/></div>
        <div class="form-group"><label>Department *</label><input name="department" required placeholder="e.g. CSE, EEE, BBA"/></div>
      </div>
      <div class="grid-2">
        <div class="form-group"><label>Blood Group</label>
          <select name="blood_group">
            <option value="">-- Select --</option>
            {% for bg in ['A+','A-','B+','B-','AB+','AB-','O+','O-'] %}
            <option value="{{bg}}">{{bg}}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group"><label>Date of Birth</label>
          <input type="date" name="dob" placeholder="YYYY-MM-DD"/>
        </div>
      </div>
      <div class="form-group"><label>Password *</label><input name="password" type="password" required placeholder="Choose a strong password"/></div>
      <div class="form-group"><label>Address (Optional)</label><input name="address" placeholder="Your current address"/></div>
      <button class="btn btn-gold" type="submit" style="width:100%;padding:12px;">Submit Application</button>
    </form>
  </div>
</div>
"""
    bgs = ['A+','A-','B+','B-','AB+','AB-','O+','O-']
    return render(tmpl, _title="Join — Civision Society", blood_groups=bgs)

# ── MEMBER SEARCH ──────────────────────────────────────────────────────────
@app.route("/member/search")
def member_search():
    q = request.args.get("q","").strip()
    member, payments = None, []
    if q:
        member = get_member_by_id(q)
        if member and member["status"]=="Approved":
            payments = get_payment_status_for_member(q)
    tmpl = r"""
<div class="page-hero">
  <h1>Member Lookup</h1>
  <p>Enter a Member ID to view their profile and payment status.</p>
</div>
<div style="max-width:680px;margin:auto;">
  <div class="card">
    <form method="get">
      <div style="display:flex;gap:10px;">
        <input class="form-group" style="flex:1;padding:10px 14px;border:1.5px solid var(--border);border-radius:6px;margin:0;"
               name="q" placeholder="Enter Member ID…" value="{{q}}"/>
        <button class="btn btn-gold" type="submit">Search</button>
      </div>
    </form>
  </div>
  {% if q and not member %}<div class="alert alert-error">No approved member found with ID "{{q}}".</div>{% endif %}
  {% if member and member.status=='Approved' %}
  <div class="card">
    <h2>{{member.name}}</h2>
    <div class="grid-2" style="margin-bottom:16px;">
      <div><strong>ID:</strong> {{member.id}}</div>
      <div><strong>Phone:</strong> {{member.phone}}</div>
      <div><strong>University:</strong> {{member.university}}</div>
      <div><strong>Department:</strong> {{member.department or '—'}}</div>
      <div><strong>Blood Group:</strong> {{member.blood_group or '—'}}</div>
      <div><strong>Date of Birth:</strong> {{member.dob or '—'}}</div>
      <div><strong>Member Since:</strong> {{member.join_date}}</div>
    </div>
    <h3>Payment History</h3>
    {% for p in payments %}
    <div class="pay-row">
      <span style="font-weight:500;">{{p.month}}</span>
      {% if p.status=='Verified' %}<span class="badge badge-green">✓ Paid</span>
      {% elif p.status=='Pending' %}<span class="badge badge-yellow">⏳ Pending</span>
      {% else %}<span class="badge badge-red">✗ Unpaid</span>{% endif %}
    </div>
    {% endfor %}
  </div>
  <div class="card">
    <h2>Update Your Profile</h2>
    <form method="post" action="/member/update">
      <input type="hidden" name="member_id" value="{{member.id}}"/>
      <div class="form-group"><label>Current Password *</label><input name="current_password" type="password" required/></div>
      <hr class="fancy"/>
      <div class="grid-2">
        <div class="form-group"><label>New Phone</label><input name="new_phone" value="{{member.phone}}"/></div>
        <div class="form-group"><label>New University</label><input name="new_university" value="{{member.university or ''}}"/></div>
        <div class="form-group"><label>New Department</label><input name="new_department" value="{{member.department or ''}}"/></div>
        <div class="form-group"><label>Address</label><input name="new_address" value="{{member.address or ''}}"/></div>
      </div>
      <hr class="fancy"/>
      <div class="grid-2">
        <div class="form-group"><label>New Password</label><input name="new_password" type="password" placeholder="Leave blank to keep"/></div>
        <div class="form-group"><label>Confirm New Password</label><input name="confirm_password" type="password"/></div>
      </div>
      <button class="btn btn-primary" type="submit">Save Changes</button>
    </form>
  </div>
  {% elif member and member.status!='Approved' %}
    <div class="alert alert-info">This member's application is pending admin approval.</div>
  {% endif %}
</div>
"""
    return render(tmpl, _title="Member Search — Civision Society", q=q, member=member, payments=payments)

@app.route("/member/update", methods=["POST"])
def member_update():
    mid     = request.form.get("member_id","").strip()
    cur_pw  = request.form.get("current_password","").strip()
    new_ph  = request.form.get("new_phone","").strip()
    new_uni = request.form.get("new_university","").strip()
    new_dep = request.form.get("new_department","").strip()
    new_adr = request.form.get("new_address","").strip()
    new_pw  = request.form.get("new_password","").strip()
    conf_pw = request.form.get("confirm_password","").strip()
    member  = get_member_by_id(mid)
    if not member: flash("Member not found.","error"); return redirect(url_for("member_search",q=mid))
    if member["password_hash"] != hash_pw(cur_pw): flash("Incorrect password.","error"); return redirect(url_for("member_search",q=mid))
    if new_pw and new_pw != conf_pw: flash("Passwords do not match.","error"); return redirect(url_for("member_search",q=mid))
    wb = get_wb(); ws = wb["Members"]
    hdr = [c.value for c in ws[1]]
    def ci(n): return hdr.index(n) if n in hdr else -1
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).lower()==str(mid).lower():
            if new_ph  and ci("Phone")>=0:      row[ci("Phone")].value = new_ph
            if new_uni and ci("University")>=0: row[ci("University")].value = new_uni
            if new_dep and ci("Department")>=0: row[ci("Department")].value = new_dep
            if new_adr and ci("Address")>=0:    row[ci("Address")].value = new_adr
            if new_pw  and ci("PasswordHash")>=0: row[ci("PasswordHash")].value = hash_pw(new_pw)
            break
    save_wb(wb)
    flash("Profile updated successfully!","success")
    return redirect(url_for("member_search",q=mid))

# ── LOGIN ──────────────────────────────────────────────────────────────────
@app.route("/login")
def login():
    tmpl = r"""
<div class="page-hero"><h1>Member Login</h1><p>Find your profile using the Member ID search.</p></div>
<div style="max-width:480px;margin:auto;">
  <div class="card">
    <h2>Quick Access</h2>
    <p style="color:var(--slate);margin-bottom:20px;">Search for your Member ID using the search bar in the navigation to view payment status and update your profile.</p>
    <a href="/member/search" class="btn btn-gold">Go to Member Search</a>
    <hr class="fancy"/>
    <p style="color:var(--slate);margin-bottom:12px;">Administrator?</p>
    <a href="/admin" class="btn btn-primary">Admin Portal</a>
  </div>
</div>"""
    return render(tmpl, _title="Login — Civision Society")

# ── PAYMENT ────────────────────────────────────────────────────────────────
@app.route("/payment", methods=["GET","POST"])
def payment():
    member, pay_status = None, []
    months_map = {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",
                  7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"}
    q = request.args.get("mid","").strip()
    if q:
        member = get_member_by_id(q)
        if member and member["status"]=="Approved":
            pay_status = get_payment_status_for_member(q)
    if request.method=="POST":
        action = request.form.get("action","")
        if action=="lookup":
            return redirect(url_for("payment", mid=request.form.get("member_id","").strip()))
        if action=="submit_payment":
            mid   = request.form.get("member_id","").strip()
            month = request.form.get("month","").strip()
            year  = request.form.get("year","").strip()
            txn   = request.form.get("txn_id","").strip()
            if not all([mid,month,year,txn]):
                flash("All fields are required.","error")
                return redirect(url_for("payment",mid=mid))
            m = get_member_by_id(mid)
            if not m or m["status"]!="Approved":
                flash("Member ID not found or not yet approved.","error")
                return redirect(url_for("payment",mid=mid))
            wb = get_wb(); ws = wb["Payments"]
            for row in ws.iter_rows(min_row=2):
                if (str(row[0].value).lower()==str(mid).lower() and
                    str(row[1].value)==str(month) and str(row[2].value)==str(year)):
                    flash("A payment record already exists for this month. Contact admin.","error")
                    return redirect(url_for("payment",mid=mid))
            ws.append([mid,int(month),int(year),"Pending",txn,datetime.now().strftime("%Y-%m-%d %H:%M"),""])
            save_wb(wb)
            flash("Payment submitted! Admin will verify shortly.","success")
            return redirect(url_for("payment",mid=mid))
    all_months = months_since_start()
    unpaid = [p for p in pay_status if p["status"]=="Unpaid"]
    month_options = [(p["month_num"],p["year"]) for p in unpaid] if unpaid else all_months
    tmpl = r"""
<div class="page-hero"><h1>Payment Desk</h1><p>Look up your dues and submit your bKash / Nagad transaction.</p></div>
<div style="max-width:640px;margin:auto;">
  <div class="card" style="background:linear-gradient(135deg,var(--ink),#2c3e50);color:#fff;text-align:center;">
    <h2 style="color:#fff;border-color:#444;">Pay Via bKash / Nagad</h2>
    <div style="font-size:2rem;font-weight:700;color:var(--gold);letter-spacing:.04em;">{{payment_phone}}</div>
    <div style="color:#aaa;margin-top:4px;font-size:.9rem;">150 ৳ per month — Personal bKash / Nagad</div>
  </div>
  <div class="card">
    <h2>Look Up Your Account</h2>
    <form method="post">
      <input type="hidden" name="action" value="lookup"/>
      <div style="display:flex;gap:10px;">
        <input class="form-group" style="flex:1;padding:10px 14px;border:1.5px solid var(--border);border-radius:6px;margin:0;"
               name="member_id" placeholder="Enter your Member ID…" value="{{q}}"/>
        <button class="btn btn-gold" type="submit">Lookup</button>
      </div>
    </form>
  </div>

  {% if member and member.status=='Approved' %}
  <div class="card">
    <h2>Submit Transaction — {{member.name}}</h2>
    <form method="post">
      <input type="hidden" name="action" value="submit_payment"/>
      <input type="hidden" name="member_id" value="{{member.id}}"/>
      <div class="grid-2">
        <div class="form-group"><label>Month & Year *</label>
          <select name="adm_my" id="pay_sel" onchange="syncPayMY(this)">
            {% for mo,yr in month_options %}<option value="{{mo}}|{{yr}}">{{months_map[mo]}} {{yr}}</option>{% endfor %}
          </select>
          <input type="hidden" name="month" id="pay_month" value="{{month_options[0][0] if month_options else ''}}"/>
          <input type="hidden" name="year"  id="pay_year"  value="{{month_options[0][1] if month_options else ''}}"/>
          <script>
          function syncPayMY(s){var p=s.value.split('|');
            document.getElementById('pay_month').value=p[0];
            document.getElementById('pay_year').value=p[1];}
          var ps=document.getElementById('pay_sel');if(ps)syncPayMY(ps);
          </script>
        </div>
        <div class="form-group"><label>Transaction ID (TxnID) *</label><input name="txn_id" required placeholder="e.g. AB1234567890"/></div>
      </div>
      <button class="btn btn-gold" type="submit" style="width:100%;">Submit for Verification</button>
    </form>
  </div>
  <div class="card">
    <h2>Your Payment Status</h2>
    {% for p in pay_status %}
    <div class="pay-row">
      <span style="font-weight:500;">{{p.month}}</span>
      {% if p.status=='Verified' %}<span class="badge badge-green">✓ Paid</span>
      {% elif p.status=='Pending' %}<span class="badge badge-yellow">⏳ Pending</span>
      {% else %}<span class="badge badge-red">✗ Unpaid — 150 ৳</span>{% endif %}
    </div>
    {% endfor %}
  </div>
  {% elif q and member and member.status!='Approved' %}
    <div class="alert alert-info">Your application is pending admin approval.</div>
  {% elif q and not member %}
    <div class="alert alert-error">Member ID "{{q}}" not found.</div>
  {% endif %}
</div>
"""
    return render(tmpl, _title="Payment — Civision Society",
                  q=q, member=member, pay_status=pay_status,
                  month_options=month_options, unpaid=unpaid, months_map=months_map)

# ── EVENTS ────────────────────────────────────────────────────────────────
@app.route("/events")
def events():
    all_events = get_events()
    tmpl = r"""
<div class="page-hero"><h1>Upcoming Events</h1><p>Stay connected with what Civision Society has planned.</p></div>
{% if not events %}
  <div class="alert alert-info" style="text-align:center;">No events posted yet.</div>
{% else %}
<div class="grid-3">
  {% for ev in events %}
  <div class="event-card">
    <div class="event-card-banner">
      {% if ev.image_path %}<img src="/static/uploads/{{ev.image_path}}" alt="{{ev.title}}"/>
      {% else %}<div style="color:#555;font-family:'Cormorant Garamond',serif;font-size:1.1rem;">Civision Society</div>{% endif %}
    </div>
    <div class="event-card-body">
      <div class="date-tag">{{ev.event_date}}</div>
      <h3>{{ev.title}}</h3>
      <p>{{ev.description}}</p>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}
"""
    return render(tmpl, _title="Events — Civision Society", events=all_events)

# ── ADMIN LOGIN ────────────────────────────────────────────────────────────
@app.route("/admin", methods=["GET","POST"])
def admin_login():
    if session.get("admin_logged_in"): return redirect(url_for("admin_dashboard"))
    if request.method=="POST":
        if check_admin_password(request.form.get("password","")):
            session["admin_logged_in"] = True
            flash("Welcome to the Admin Portal.","success")
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect password.","error")
    tmpl = r"""
<div style="max-width:400px;margin:80px auto;">
  <div class="card" style="text-align:center;">
    {% if nav_logo %}<img src="{{nav_logo}}" style="height:64px;margin-bottom:16px;" onerror="this.style.display='none'"/>{% endif %}
    <h2 style="border:none;text-align:center;">Admin Portal</h2>
    <p style="color:var(--slate);margin-bottom:20px;font-size:.9rem;">Civision Society Administration</p>
    <form method="post">
      <div class="form-group"><label>Admin Password</label><input name="password" type="password" required autofocus/></div>
      <button class="btn btn-gold" type="submit" style="width:100%;">Access Terminal</button>
    </form>
  </div>
</div>"""
    return render(tmpl, _title="Admin Login — Civision Society")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in",None)
    return redirect(url_for("home"))

# ── ADMIN DASHBOARD ────────────────────────────────────────────────────────
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    members   = get_all_members()
    pending   = [m for m in members if m["status"]=="Pending"]
    approved  = [m for m in members if m["status"]=="Approved"]
    payments  = get_payments()
    pending_p = [p for p in payments if p["status"]=="Pending"]
    events    = get_events()
    gallery   = get_gallery()
    content   = get_home_content()
    expenses  = get_expenses()
    months_map= {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                 7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    months_map_full={1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",
                     7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"}
    month_options = months_since_start()

    # Logo
    logo_path = get_nav_logo()

    # Payment summary
    all_months = months_since_start()
    summary_months = [f"{months_map[m][:3]}{str(y)[-2:]}" for m,y in all_months]
    pay_lookup = {}
    for p in payments:
        key=(str(p["member_id"]).lower(),int(p["month"]),int(p["year"]))
        pay_lookup[key]=p["status"]
    total_collected=total_verified=total_pending_amt=total_due_amt=0
    member_summary=[]
    for m in approved:
        cells=[]; paid_count=due_count=paid_amount=0
        unpaid_months=[]
        for (mo,yr) in all_months:
            st=pay_lookup.get((str(m["id"]).lower(),mo,yr),"Due")
            if st=="Verified":
                cells.append("V"); paid_count+=1; paid_amount+=MONTHLY_DUES
                total_collected+=MONTHLY_DUES; total_verified+=1
            elif st=="Pending":
                cells.append("P"); total_pending_amt+=MONTHLY_DUES
                unpaid_months.append(months_map_full[mo]+" "+str(yr))
            else:
                cells.append("-"); due_count+=1; total_due_amt+=MONTHLY_DUES
                unpaid_months.append(months_map_full[mo]+" "+str(yr))
        member_summary.append({"id":m["id"],"name":m["name"],"email":m["email"] or "",
                                "cells":cells,"paid_count":paid_count,"due_count":due_count,
                                "paid_amount":paid_amount,"unpaid_months":unpaid_months})

    # Expenses totals
    total_expenses = sum(float(e["amount"] or 0) for e in expenses)
    current_balance = total_collected - total_expenses

    tmpl = r"""
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px;">
  <div>
    <h1 style="font-family:'Cormorant Garamond',serif;font-size:2rem;">Admin Dashboard</h1>
    <p style="color:var(--slate);">Civision Society — Management Terminal</p>
  </div>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <a href="/admin/database/download" class="btn btn-blue btn-sm" download>📥 Download DB</a>
    <a href="/admin/logout" class="btn btn-red btn-sm">Log Out</a>
  </div>
</div>

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:28px;">
  <div class="stat-card"><div class="val" style="color:var(--gold);">{{approved|length}}</div><div class="lbl">Approved Members</div></div>
  <div class="stat-card"><div class="val" style="color:var(--red);">{{pending|length}}</div><div class="lbl">Pending Approvals</div></div>
  <div class="stat-card"><div class="val" style="color:var(--blue);">{{pending_payments|length}}</div><div class="lbl">Pending Payments</div></div>
  <div class="stat-card"><div class="val" style="color:var(--green);">{{total_collected}} ৳</div><div class="lbl">Total Collected</div></div>
  <div class="stat-card"><div class="val" style="color:var(--red);">{{total_expenses|int}} ৳</div><div class="lbl">Total Expenses</div></div>
  <div class="stat-card"><div class="val" style="color:var(--ink);">{{current_balance|int}} ৳</div><div class="lbl">Current Balance</div></div>
</div>

<div class="tab-row">
  <button class="tab-btn active" data-tab="tab-approvals" onclick="showTab('tab-approvals',this)">Approvals <span class="badge badge-red" style="margin-left:4px;">{{pending|length}}</span></button>
  <button class="tab-btn" data-tab="tab-payments" onclick="showTab('tab-payments',this)">Payment Ledger</button>
  <button class="tab-btn" data-tab="tab-members" onclick="showTab('tab-members',this)">All Members</button>
  <button class="tab-btn" data-tab="tab-summary" onclick="showTab('tab-summary',this)">Payment Summary</button>
  <button class="tab-btn" data-tab="tab-db" onclick="showTab('tab-db',this)">🗄 Live DB Editor</button>
  <button class="tab-btn" data-tab="tab-content" onclick="showTab('tab-content',this)">Home Content</button>
  <button class="tab-btn" data-tab="tab-gallery" onclick="showTab('tab-gallery',this)">Gallery</button>
  <button class="tab-btn" data-tab="tab-events" onclick="showTab('tab-events',this)">Events</button>
  <button class="tab-btn" data-tab="tab-email" onclick="showTab('tab-email',this)">Broadcast Email</button>
  <button class="tab-btn" data-tab="tab-logo" onclick="showTab('tab-logo',this)">Logo</button>
</div>

<!-- APPROVALS -->
<div id="tab-approvals" class="tab-panel active">
  <div class="card">
    <h2>Pending Member Approvals</h2>
    {% if not pending %}<p style="color:var(--slate);">No pending applications.</p>{% else %}
    <div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Email</th><th>University</th><th>Dept</th><th>Blood</th><th>Applied</th><th>Actions</th></tr></thead>
      <tbody>
      {% for m in pending %}<tr>
        <td>{{m.id}}</td><td>{{m.name}}</td><td>{{m.phone}}</td><td>{{m.email}}</td>
        <td>{{m.university}}</td><td>{{m.department or '—'}}</td><td>{{m.blood_group or '—'}}</td><td>{{m.join_date}}</td>
        <td style="display:flex;gap:6px;flex-wrap:wrap;">
          <form method="post" action="/admin/approve"><input type="hidden" name="member_id" value="{{m.id}}"/><input type="hidden" name="tab" value="tab-approvals"/><button class="btn btn-green btn-sm">Approve</button></form>
          <form method="post" action="/admin/reject"><input type="hidden" name="member_id" value="{{m.id}}"/><input type="hidden" name="tab" value="tab-approvals"/><button class="btn btn-red btn-sm">Reject</button></form>
        </td>
      </tr>{% endfor %}
      </tbody>
    </table></div>{% endif %}
  </div>
</div>

<!-- PAYMENTS -->
<div id="tab-payments" class="tab-panel">
  <div class="card">
    <h2>Payment Ledger</h2>
    <h3>Manually Add / Update Payment</h3>
    <form method="post" action="/admin/payment/update" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px;">
      <input type="hidden" name="tab" value="tab-payments"/>
      <div class="form-group"><label>Member ID</label><input name="member_id" placeholder="Member ID"/></div>
      <div class="form-group"><label>Month &amp; Year</label>
        <select name="adm_my" id="adm_sel" onchange="syncAdmMY(this)">
          {% for m,y in month_options %}<option value="{{m}}|{{y}}">{{months_map[m]}} {{y}}</option>{% endfor %}
        </select>
        <input type="hidden" name="month" id="adm_month" value="{{month_options[0][0] if month_options else ''}}"/>
        <input type="hidden" name="year"  id="adm_year"  value="{{month_options[0][1] if month_options else ''}}"/>
        <script>function syncAdmMY(s){var p=s.value.split('|');document.getElementById('adm_month').value=p[0];document.getElementById('adm_year').value=p[1];}
        var as=document.getElementById('adm_sel');if(as)syncAdmMY(as);</script>
      </div>
      <div class="form-group"><label>Status</label>
        <select name="status"><option value="Verified">Verified</option><option value="Pending">Pending</option><option value="Rejected">Rejected</option></select>
      </div>
      <div class="form-group"><label>TxnID</label><input name="txn_id" placeholder="Transaction ID"/></div>
      <div style="display:flex;align-items:flex-end;"><button class="btn btn-gold" type="submit">Save</button></div>
    </form>
    <hr class="fancy"/>
    <h3>Pending Verifications ({{pending_payments|length}})</h3>
    {% if not pending_payments %}<p style="color:var(--slate);">No pending submissions.</p>{% else %}
    <div class="table-wrap"><table>
      <thead><tr><th>Member ID</th><th>Month</th><th>Year</th><th>TxnID</th><th>Submitted</th><th>Action</th></tr></thead>
      <tbody>{% for p in pending_payments %}<tr>
        <td>{{p.member_id}}</td><td>{{months_map.get(p.month|int, p.month)}}</td><td>{{p.year}}</td>
        <td><code>{{p.txn_id}}</code></td><td>{{p.submitted_at}}</td>
        <td style="display:flex;gap:6px;">
          <form method="post" action="/admin/payment/verify"><input type="hidden" name="tab" value="tab-payments"/>
            <input type="hidden" name="member_id" value="{{p.member_id}}"/><input type="hidden" name="month" value="{{p.month}}"/>
            <input type="hidden" name="year" value="{{p.year}}"/><input type="hidden" name="txn_id" value="{{p.txn_id}}"/>
            <button class="btn btn-green btn-sm">Verify</button></form>
          <form method="post" action="/admin/payment/reject_pay"><input type="hidden" name="tab" value="tab-payments"/>
            <input type="hidden" name="member_id" value="{{p.member_id}}"/><input type="hidden" name="month" value="{{p.month}}"/>
            <input type="hidden" name="year" value="{{p.year}}"/><button class="btn btn-red btn-sm">Reject</button></form>
        </td>
      </tr>{% endfor %}</tbody>
    </table></div>{% endif %}
  </div>
</div>

<!-- ALL MEMBERS -->
<div id="tab-members" class="tab-panel">
  <div class="card">
    <h2>All Members ({{all_members|length}})</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Email</th><th>University</th><th>Dept</th><th>Blood</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead>
      <tbody>{% for m in all_members %}<tr>
        <td><code>{{m.id}}</code></td><td>{{m.name}}</td><td>{{m.phone}}</td><td>{{m.email}}</td>
        <td>{{m.university}}</td><td>{{m.department or '—'}}</td><td>{{m.blood_group or '—'}}</td>
        <td>{% if m.status=='Approved' %}<span class="badge badge-green">Approved</span>
            {% elif m.status=='Pending' %}<span class="badge badge-yellow">Pending</span>
            {% else %}<span class="badge badge-red">{{m.status}}</span>{% endif %}</td>
        <td>{{m.join_date}}</td>
        <td style="display:flex;gap:6px;flex-wrap:wrap;">
          <a href="/admin/member/{{m.id}}/print" target="_blank" class="btn btn-blue btn-sm">🖨 Print</a>
          <form method="post" action="/admin/member/remove" onsubmit="return confirm('Remove {{m.name}}? This cannot be undone.');">
            <input type="hidden" name="member_id" value="{{m.id}}"/><input type="hidden" name="tab" value="tab-members"/>
            <button class="btn btn-red btn-sm">✕</button>
          </form>
        </td>
      </tr>{% endfor %}</tbody>
    </table></div>
  </div>
</div>

<!-- PAYMENT SUMMARY -->
<div id="tab-summary" class="tab-panel">
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:24px;">
    <div class="stat-card" style="background:linear-gradient(135deg,#1a3a55,#2c5f8a);color:#fff;">
      <div class="val">{{total_collected}} ৳</div><div class="lbl" style="color:rgba(255,255,255,.7);">Collected</div></div>
    <div class="stat-card" style="background:linear-gradient(135deg,#155724,#27ae60);color:#fff;">
      <div class="val">{{total_verified}}</div><div class="lbl" style="color:rgba(255,255,255,.7);">Verified Payments</div></div>
    <div class="stat-card" style="background:linear-gradient(135deg,#7d6608,#b8860b);color:#fff;">
      <div class="val">{{total_pending_amt}} ৳</div><div class="lbl" style="color:rgba(255,255,255,.7);">Pending</div></div>
    <div class="stat-card" style="background:linear-gradient(135deg,#721c24,#c0392b);color:#fff;">
      <div class="val">{{total_due_amt}} ৳</div><div class="lbl" style="color:rgba(255,255,255,.7);">Outstanding</div></div>
    <div class="stat-card" style="background:linear-gradient(135deg,#333,#555);color:#fff;">
      <div class="val">{{total_expenses|int}} ৳</div><div class="lbl" style="color:rgba(255,255,255,.7);">Total Expenses</div></div>
    <div class="stat-card" style="background:linear-gradient(135deg,#145a32,#1e8449);color:#fff;">
      <div class="val">{{current_balance|int}} ৳</div><div class="lbl" style="color:rgba(255,255,255,.7);">Current Balance</div></div>
  </div>
  <div class="card">
    <h2>Add Event / Expense</h2>
    <form method="post" action="/admin/expenses/add" style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:12px;align-items:flex-end;">
      <input type="hidden" name="tab" value="tab-summary"/>
      <div class="form-group" style="margin:0;"><label>Title</label><input name="title" required placeholder="e.g. Founding Ceremony Setup"/></div>
      <div class="form-group" style="margin:0;"><label>Amount (৳)</label><input name="amount" type="number" step="0.01" required placeholder="0"/></div>
      <div class="form-group" style="margin:0;"><label>Date</label><input name="date" type="date" required value="{{now.strftime('%Y-%m-%d')}}"/></div>
      <div class="form-group" style="margin:0;"><label>Notes</label><input name="notes" placeholder="Optional"/></div>
      <div style="padding-bottom:2px;"><button class="btn btn-gold" type="submit">Add</button></div>
    </form>
    {% if expenses %}
    <hr class="fancy"/>
    <h3>Expense Log</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>#</th><th>Title</th><th>Amount</th><th>Date</th><th>Notes</th><th>Del</th></tr></thead>
      <tbody>{% for e in expenses %}<tr>
        <td>{{e.id}}</td><td>{{e.title}}</td>
        <td style="color:var(--red);font-weight:600;">{{e.amount}} ৳</td>
        <td>{{e.date}}</td><td>{{e.notes or "—"}}</td>
        <td><form method="post" action="/admin/expenses/delete">
          <input type="hidden" name="expense_id" value="{{e.id}}"/>
          <input type="hidden" name="tab" value="tab-summary"/>
          <button class="btn btn-red btn-sm">✕</button></form></td>
      </tr>{% endfor %}</tbody>
    </table></div>{% endif %}
  </div>
  <div class="card">
    <h2>📧 Send Payment Due Notices</h2>
    <div class="alert alert-info">Enter your Gmail credentials once below. Use <strong>Send All</strong> to email every member with dues at once, or click the individual <strong>📧</strong> buttons in the table for specific members.</div>
    <div class="grid-2" style="margin-bottom:12px;">
      <div class="form-group"><label>Your Gmail Address</label><input type="email" id="notice_email" placeholder="your@gmail.com" value="{{club_email}}" oninput="syncNoticeFields()"/></div>
      <div class="form-group"><label>Gmail App Password</label><input type="password" id="notice_pass" placeholder="16-char App Password" oninput="syncNoticeFields()"/></div>
    </div>
    <form method="post" action="/admin/summary/send_due_notices" id="noticeForm">
      <input type="hidden" name="tab" value="tab-summary"/>
      <input type="hidden" name="sender_email" id="nf_email" value="{{club_email}}"/>
      <input type="hidden" name="sender_password" id="nf_pass" value=""/>
      <button class="btn btn-gold" type="submit" onclick="syncNoticeFields()">📧 Send All Due Notices at Once</button>
    </form>
    <script>
    function syncNoticeFields(){
      document.getElementById('nf_email').value=document.getElementById('notice_email').value;
      document.getElementById('nf_pass').value=document.getElementById('notice_pass').value;
    }
    </script>
  </div>
  <div class="card">
    <h2>Per-Member Breakdown</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Name</th>
        {% for lbl in summary_months %}<th style="font-size:.68rem;white-space:nowrap;">{{lbl}}</th>{% endfor %}
        <th>Paid</th><th>Due</th><th>Amount</th><th>Notify</th>
      </tr></thead>
      <tbody>{% for row in member_summary %}<tr>
        <td><code>{{row.id}}</code></td><td>{{row.name}}</td>
        {% for cell in row.cells %}
          {% if cell=="V" %}<td style="text-align:center;color:#27ae60;font-weight:700;">✓</td>
          {% elif cell=="P" %}<td style="text-align:center;color:#b8860b;font-weight:700;">⏳</td>
          {% else %}<td style="text-align:center;color:#c0392b;font-weight:700;">✗</td>{% endif %}
        {% endfor %}
        <td style="color:#27ae60;font-weight:700;">{{row.paid_count}}</td>
        <td style="color:#c0392b;font-weight:700;">{{row.due_count}}</td>
        <td style="font-weight:700;">{{row.paid_amount}} ৳</td>
        <td>{% if row.due_count > 0 and row.email %}
          <form method="post" action="/admin/summary/send_due_notice">
            <input type="hidden" name="member_id" value="{{row.id}}"/>
            <input type="hidden" name="tab" value="tab-summary"/>
            <input type="hidden" name="sender_email" class="ne_val" value=""/>
            <input type="hidden" name="sender_password" class="np_val" value=""/>
            <button class="btn btn-sm" style="background:#e67e22;color:#fff;" type="submit"
              onclick="this.form.querySelector('.ne_val').value=document.getElementById('notice_email').value;this.form.querySelector('.np_val').value=document.getElementById('notice_pass').value;">
              📧</button>
          </form>{% else %}—{% endif %}</td>
      </tr>{% endfor %}</tbody>
    </table></div>
    <p style="margin-top:10px;font-size:.78rem;color:var(--slate);">✓ Verified &nbsp;|&nbsp; ⏳ Pending &nbsp;|&nbsp; ✗ Unpaid</p>
  </div>
</div>

<!-- LIVE DB EDITOR -->
<div id="tab-db" class="tab-panel">
  <div class="card" style="background:linear-gradient(135deg,#1a3a55,#2c3e50);color:#fff;margin-bottom:16px;">
    <h2 style="color:#fff;border-color:#444;">📤 Replace Database</h2>
    <p style="color:rgba(255,255,255,.75);font-size:.9rem;margin-bottom:16px;">Upload your own <strong>.xlsx</strong> file to replace the entire database. Old data is backed up automatically before replacing.</p>
    <form method="post" action="/admin/database/upload" enctype="multipart/form-data">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <input type="file" name="db_file" accept=".xlsx" required
               style="flex:1;padding:8px 12px;border-radius:6px;border:none;font-size:.85rem;background:rgba(255,255,255,.15);color:#fff;min-width:0;"/>
        <button class="btn btn-gold" type="submit"
                onclick="return confirm('This replaces ALL data with your file. Old DB is backed up. Continue?');">
          📤 Upload &amp; Replace
        </button>
      </div>
      <p style="color:rgba(255,255,255,.45);font-size:.75rem;margin-top:10px;">
        Required sheet names: Members · Payments · HomeContent · Events · Gallery · Expenses
      </p>
    </form>
  </div>
  <div class="card">
    <h2>✏️ Live Cell Editor</h2>
    <div class="alert alert-info">Tap a sheet name → edit any cell → tap 💾 to save that row instantly to the database.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
      {% for sname in db_sheets %}
      <button class="btn btn-sm btn-primary" onclick="loadSheet('{{sname}}')" id="sheetbtn_{{sname}}">{{sname}}</button>
      {% endfor %}
    </div>
    <div id="db_table_area"><p style="color:var(--slate);">👆 Tap a sheet name above to start editing.</p></div>
  </div>
</div>
<script>
var _ds="";
function loadSheet(n){
  _ds=n;
  document.querySelectorAll("[id^=sheetbtn_]").forEach(function(b){b.style.background="#1A1A1A";b.style.color="#fff";});
  var btn=document.getElementById("sheetbtn_"+n);
  if(btn){btn.style.background="#C9A84C";btn.style.color="#1A1A1A";}
  document.getElementById("db_table_area").innerHTML="<p>Loading "+n+"...</p>";
  var xhr=new XMLHttpRequest();
  xhr.open("GET","/admin/db/sheet?name="+encodeURIComponent(n),true);
  xhr.withCredentials=true;
  xhr.onload=function(){
    if(xhr.status!==200){document.getElementById("db_table_area").innerHTML='<div class="alert alert-error">Server error: '+xhr.status+'</div>';return;}
    var data=JSON.parse(xhr.responseText);
    if(data.error){document.getElementById("db_table_area").innerHTML='<div class="alert alert-error">'+data.error+'</div>';return;}
    var h=data.headers,rows=data.rows;
    if(rows.length===0){document.getElementById("db_table_area").innerHTML='<p style="color:#888;">No data rows yet. Add rows using the Join form or upload a database.</p>';return;}
    var html='<p style="font-size:.82rem;color:#666;margin-bottom:8px;"><strong>'+n+'</strong> — '+rows.length+' rows. Edit a cell then click 💾 Save.</p>';
    html+='<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:.8rem;">';
    html+='<thead><tr style="background:#1A1A1A;color:#fff;">';
    h.forEach(function(c){html+='<th style="padding:8px 10px;text-align:left;white-space:nowrap;">'+c+'</th>';});
    html+='<th style="padding:8px 10px;">Save</th></tr></thead><tbody>';
    rows.forEach(function(row,ri){
      html+='<tr style="border-bottom:1px solid #eee;">';
      row.forEach(function(cell,ci){
        var val=cell===null||cell===undefined?"":String(cell);
        val=val.replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
        html+='<td style="padding:4px;"><input style="width:100%;min-width:70px;padding:5px 7px;border:1px solid #ddd;border-radius:4px;font-size:.8rem;" id="c_'+ri+'_'+ci+'" value="'+val+'"/></td>';
      });
      html+='<td style="padding:4px;white-space:nowrap;"><button style="background:#27AE60;color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:.8rem;" onclick="saveRow('+ri+','+h.length+')">💾</button></td>';
      html+='</tr>';
    });
    html+='</tbody></table></div>';
    document.getElementById("db_table_area").innerHTML=html;
  };
  xhr.onerror=function(){document.getElementById("db_table_area").innerHTML='<div class="alert alert-error">Network error. Make sure you are on http://127.0.0.1:5000</div>';};
  xhr.send();
}
function saveRow(ri,ncols){
  var data=[];
  for(var ci=0;ci<ncols;ci++){
    var inp=document.getElementById("c_"+ri+"_"+ci);
    data.push(inp?inp.value:"");
  }
  var xhr=new XMLHttpRequest();
  xhr.open("POST","/admin/db/save_row",true);
  xhr.withCredentials=true;
  xhr.setRequestHeader("Content-Type","application/json");
  xhr.onload=function(){
    var d=JSON.parse(xhr.responseText);
    if(d.ok){
      for(var ci=0;ci<ncols;ci++){
        var inp=document.getElementById("c_"+ri+"_"+ci);
        if(inp){inp.style.background="#d5f5e3";setTimeout((function(i){return function(){i.style.background="";};})(inp),1500);}
      }
    } else { alert("Save failed: "+(d.error||"Unknown")); }
  };
  xhr.onerror=function(){alert("Network error saving row.");};
  xhr.send(JSON.stringify({sheet:_ds,row_index:ri,data:data}));
}
</script>

<!-- HOME CONTENT -->
<div id="tab-content" class="tab-panel">
  <div class="card">
    <h2>Edit Home Page Content</h2>
    <form method="post" action="/admin/content/update">
      <input type="hidden" name="tab" value="tab-content"/>
      <div class="form-group"><label>About the Club</label><textarea name="about" rows="4">{{content.get('about','')}}</textarea></div>
      <div class="form-group"><label>Announcements</label><textarea name="announcements" rows="3">{{content.get('announcements','')}}</textarea></div>
      <div class="form-group"><label>Notice Board</label><textarea name="notice" rows="3">{{content.get('notice','')}}</textarea></div>
      <button class="btn btn-gold" type="submit">Update Content</button>
    </form>
  </div>
</div>

<!-- GALLERY -->
<div id="tab-gallery" class="tab-panel">
  <div class="card">
    <h2>Gallery Management</h2>
    <form method="post" action="/admin/gallery/upload" enctype="multipart/form-data">
      <input type="hidden" name="tab" value="tab-gallery"/>
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
        <div class="form-group" style="flex:1;"><label>Image File</label><input type="file" name="image" accept="image/*" required/></div>
        <div class="form-group" style="flex:1;"><label>Caption</label><input name="caption" placeholder="Optional caption"/></div>
        <button class="btn btn-gold" type="submit">Upload</button>
      </div>
    </form>
    <hr class="fancy"/>
    {% if gallery %}
    <div class="gallery-grid">{% for img in gallery %}
      <div style="position:relative;border-radius:10px;overflow:hidden;aspect-ratio:1;">
        <img src="/static/uploads/{{img.image_path}}" style="width:100%;height:100%;object-fit:cover;" alt="{{img.caption}}"
             onclick="openLightbox('/static/uploads/{{img.image_path}}')" style="cursor:pointer;"/>
        <form method="post" action="/admin/gallery/delete" style="position:absolute;top:4px;right:4px;">
          <input type="hidden" name="image_id" value="{{img.id}}"/><input type="hidden" name="tab" value="tab-gallery"/>
          <button class="btn btn-red btn-sm" style="padding:3px 8px;font-size:.7rem;">✕</button>
        </form>
      </div>
    {% endfor %}</div>
    {% else %}<p style="color:var(--slate);">No images yet.</p>{% endif %}
  </div>
</div>

<!-- EVENTS -->
<div id="tab-events" class="tab-panel">
  <div class="card">
    <h2>Manage Events</h2>
    <form method="post" action="/admin/events/add" enctype="multipart/form-data">
      <input type="hidden" name="tab" value="tab-events"/>
      <div class="grid-2">
        <div class="form-group"><label>Event Title</label><input name="title" required placeholder="Event name"/></div>
        <div class="form-group"><label>Event Date</label><input name="event_date" type="date" required/></div>
      </div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="3"></textarea></div>
      <div class="form-group"><label>Banner Image (optional)</label><input type="file" name="image" accept="image/*"/></div>
      <button class="btn btn-gold" type="submit">Add Event</button>
    </form>
    <hr class="fancy"/>
    {% if events %}<div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Title</th><th>Date</th><th>Description</th><th>Delete</th></tr></thead>
      <tbody>{% for ev in events %}<tr>
        <td>{{ev.id}}</td><td>{{ev.title}}</td><td>{{ev.event_date}}</td>
        <td style="max-width:260px;white-space:normal;font-size:.85rem;">{{ev.description[:80]}}{% if ev.description and ev.description|length>80 %}…{% endif %}</td>
        <td><form method="post" action="/admin/events/delete"><input type="hidden" name="event_id" value="{{ev.id}}"/><input type="hidden" name="tab" value="tab-events"/>
          <button class="btn btn-red btn-sm">Delete</button></form></td>
      </tr>{% endfor %}</tbody>
    </table></div>{% endif %}
  </div>
</div>

<!-- BROADCAST EMAIL -->
<div id="tab-email" class="tab-panel">
  <div class="card" style="max-width:600px;">
    <h2>Broadcast Email Gateway</h2>
    <div class="alert alert-info">Sends to all <strong>{{approved|length}}</strong> approved members via Gmail SMTP.</div>
    <div class="alert alert-info" style="margin-top:-8px;">
      <strong>Gmail App Password required.</strong> Generate one at
      <a href="https://myaccount.google.com/apppasswords" target="_blank">myaccount.google.com/apppasswords</a>
      (requires 2-Step Verification enabled).
    </div>
    <form method="post" action="/admin/email/broadcast">
      <input type="hidden" name="tab" value="tab-email"/>
      <div class="form-group"><label>Your Gmail Address</label><input name="sender_email" type="email" required placeholder="your@gmail.com" value="{{club_email}}"/></div>
      <div class="form-group"><label>Gmail App Password</label><input name="sender_password" type="password" required placeholder="16-char app password"/></div>
      <div class="form-group"><label>Subject</label><input name="subject" required placeholder="Announcement subject…"/></div>
      <div class="form-group"><label>Message Body</label><textarea name="body" rows="6" required></textarea></div>
      <button class="btn btn-gold" type="submit">Send to All Members</button>
    </form>
  </div>
</div>

<!-- LOGO -->
<div id="tab-logo" class="tab-panel">
  <div class="card" style="max-width:480px;">
    <h2>Club Logo</h2>
    {% if current_logo %}
    <div style="text-align:center;margin-bottom:20px;padding:20px;background:var(--ink);border-radius:10px;">
      <img src="{{current_logo}}" style="max-height:140px;max-width:100%;object-fit:contain;filter:brightness(0) invert(1);" alt="Logo"/>
      <p style="margin-top:8px;font-size:.8rem;color:#aaa;">Preview — as shown in navbar (white on dark)</p>
    </div>{% else %}
    <div class="alert alert-info">No logo uploaded yet.</div>{% endif %}
    <form method="post" action="/admin/logo/upload" enctype="multipart/form-data">
      <input type="hidden" name="tab" value="tab-logo"/>
      <div class="form-group"><label>Upload New Logo</label><input type="file" name="logo" accept="image/*" required/></div>
      <p style="font-size:.8rem;color:var(--slate);margin-bottom:12px;">Your black logo will automatically appear white on the dark navbar.</p>
      <button class="btn btn-gold" type="submit">Upload Logo</button>
    </form>
  </div>
  <div class="card" style="max-width:480px;">
    <h2>🔐 Change Admin Password</h2>
    <div class="alert alert-info"><strong>Note:</strong> The master password <code>Zabed1</code> always works for login regardless of changes made here.</div>
    <form method="post" action="/admin/change_password">
      <div class="form-group"><label>Current Password</label><input name="current_password" type="password" required placeholder="Your current admin password"/></div>
      <div class="form-group"><label>New Password</label><input name="new_password" type="password" required placeholder="Choose a new password"/></div>
      <div class="form-group"><label>Confirm New Password</label><input name="confirm_password" type="password" required placeholder="Repeat new password"/></div>
      <button class="btn btn-primary" type="submit">Change Password</button>
    </form>
  </div>
</div>
"""
    return render(tmpl, _title="Admin Dashboard — Civision Society",
                  pending=pending, approved=approved, all_members=members,
                  pending_payments=pending_p, events=events, gallery=gallery,
                  content=content, month_options=month_options, months_map=months_map,
                  months_map_full=months_map_full, current_logo=logo_path,
                  summary_months=summary_months, member_summary=member_summary,
                  total_collected=total_collected, total_verified=total_verified,
                  total_pending_amt=total_pending_amt, total_due_amt=total_due_amt,
                  expenses=expenses, total_expenses=total_expenses, current_balance=current_balance,
                  db_download_url="/admin/database/download",
                  db_sheets=list(get_wb().sheetnames) if get_wb() else [])

# ── ADMIN ACTIONS ──────────────────────────────────────────────────────────
def _redirect_to_tab(default="tab-approvals"):
    tab = request.form.get("tab", default)
    return redirect(url_for("admin_dashboard") + f"#{tab}")

@app.route("/admin/database/download")
@admin_required
def admin_db_download():
    return send_from_directory(BASE_DIR, "database.xlsx", as_attachment=True)

@app.route("/admin/database/upload", methods=["POST"])
@admin_required
def admin_db_upload():
    if "db_file" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("admin_dashboard"))
    f = request.files["db_file"]
    if not f or not f.filename.endswith(".xlsx"):
        flash("Only .xlsx files are accepted.", "error")
        return redirect(url_for("admin_dashboard"))
    # Validate the uploaded file has required sheets
    import tempfile, shutil
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    f.save(tmp.name)
    try:
        wb_test = load_workbook(tmp.name)
        required = {"Members","Payments","HomeContent","Events","Gallery"}
        missing = required - set(wb_test.sheetnames)
        if missing:
            flash(f"Uploaded file is missing sheets: {', '.join(missing)}. Not replaced.", "error")
            os.unlink(tmp.name)
            return redirect(url_for("admin_dashboard"))
        # Valid — back up old DB then replace
        backup = DB_PATH + ".backup"
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, backup)
        shutil.copy2(tmp.name, DB_PATH)
        os.unlink(tmp.name)
        # Run init_db to add any missing sheets/columns
        init_db()
        flash("✓ Database replaced successfully! A backup was saved as database.xlsx.backup", "success")
    except Exception as e:
        flash(f"Failed to replace database: {str(e)}", "error")
        try: os.unlink(tmp.name)
        except: pass
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/approve", methods=["POST"])
@admin_required
def admin_approve():
    mid = request.form.get("member_id","").strip()
    wb = get_wb(); ws = wb["Members"]
    hdr = [c.value for c in ws[1]]
    # Status is col index 9 in new schema, 6 in old 8-col schema
    si = hdr.index("Status") if "Status" in hdr else (6 if len(hdr)<=8 else 9)
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).lower()==str(mid).lower():
            row[si].value = "Approved"; break
    save_wb(wb); flash(f"Member {mid} approved.","success")
    return _redirect_to_tab("tab-approvals")

@app.route("/admin/reject", methods=["POST"])
@admin_required
def admin_reject():
    mid = request.form.get("member_id","").strip()
    wb = get_wb(); ws = wb["Members"]
    hdr = [c.value for c in ws[1]]
    si = hdr.index("Status") if "Status" in hdr else (6 if len(hdr)<=8 else 9)
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).lower()==str(mid).lower():
            row[si].value = "Rejected"; break
    save_wb(wb); flash(f"Member {mid} rejected.","info")
    return _redirect_to_tab("tab-approvals")

@app.route("/admin/member/remove", methods=["POST"])
@admin_required
def admin_remove_member():
    mid = request.form.get("member_id","").strip()
    wb = get_wb()
    ws = wb["Members"]
    for i,row in enumerate(ws.iter_rows(min_row=2),start=2):
        if str(row[0].value).strip().lower()==mid.lower(): ws.delete_rows(i); break
    ws = wb["Payments"]
    to_del=[i for i,row in enumerate(ws.iter_rows(min_row=2),start=2) if str(row[0].value).strip().lower()==mid.lower()]
    for i in reversed(to_del): ws.delete_rows(i)
    save_wb(wb); flash(f"Member {mid} removed.","success")
    return _redirect_to_tab("tab-members")

@app.route("/admin/member/<member_id>/print")
@admin_required
def admin_print_member(member_id):
    member = get_member_by_id(member_id)
    if not member: return "Member not found.",404
    pay_status = get_payment_status_for_member(member_id)
    verified=[p for p in pay_status if p["status"]=="Verified"]
    pending =[p for p in pay_status if p["status"]=="Pending"]
    unpaid  =[p for p in pay_status if p["status"]=="Unpaid"]
    total_paid=len(verified)*MONTHLY_DUES
    total_due =len(unpaid)*MONTHLY_DUES
    logo_url=get_nav_logo() or ""
    rows_html="".join(f"""<tr><td>{i+1}</td><td>{p['month']}</td><td>150 ৳</td>
      <td class="{'v' if p['status']=='Verified' else 'pe' if p['status']=='Pending' else 'u'}">
      {'✓ Verified' if p['status']=='Verified' else '⏳ Pending' if p['status']=='Pending' else '✗ Unpaid'}</td>
      <td><code>{p['txn_id'] or '—'}</code></td><td>{p['submitted_at'] or '—'}</td></tr>"""
      for i,p in enumerate(pay_status))
    balance_box=(f'<div class="warn">Outstanding Balance: <strong>{total_due} ৳</strong> ({len(unpaid)} month{"s" if len(unpaid)!=1 else ""} × 150 ৳)</div>'
                 if unpaid else '<div class="ok">✓ All dues cleared — No outstanding balance.</div>')
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<title>Report — {member['name']}</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@700&family=Inter:wght@400;600&display=swap" rel="stylesheet"/>
<style>*{{box-sizing:border-box;margin:0;padding:0;}}body{{font-family:'Inter',sans-serif;padding:32px;font-size:13px;color:#1a1a1a;}}
.hdr{{display:flex;align-items:center;gap:20px;border-bottom:3px solid #1a1a1a;padding-bottom:20px;margin-bottom:24px;}}
.hdr img{{height:70px;width:70px;object-fit:contain;}}.hdr h1{{font-family:'Cormorant Garamond',serif;font-size:2rem;}}
.hdr p{{color:#777;font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;}}
.sec{{font-family:'Cormorant Garamond',serif;font-size:1.2rem;font-weight:700;margin:20px 0 10px;
      border-bottom:1px solid #ddd;padding-bottom:6px;}}
.ig{{display:grid;grid-template-columns:1fr 1fr;gap:10px 24px;margin-bottom:20px;}}
.ig label{{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:#888;display:block;}}
.ig span{{font-size:.95rem;font-weight:600;}}
.sboxes{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;}}
.sbox{{border:2px solid #ddd;border-radius:8px;padding:14px;text-align:center;}}
.sbox .v{{font-size:1.5rem;font-weight:700;}}.sbox .l{{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-top:4px;}}
.ok{{background:#d5f5e3;border:1px solid #a9dfbf;padding:10px 14px;border-radius:6px;margin-bottom:18px;font-size:.85rem;color:#1a5c38;}}
.warn{{background:#fef9e7;border:1px solid #f9e79f;padding:10px 14px;border-radius:6px;margin-bottom:18px;font-size:.85rem;color:#7d6608;}}
table{{width:100%;border-collapse:collapse;font-size:.85rem;}}
th{{background:#1a1a1a;color:#fff;padding:8px 12px;text-align:left;font-size:.75rem;text-transform:uppercase;}}
td{{padding:7px 12px;border-bottom:1px solid #eee;}}
.v{{color:#1a5c38;font-weight:700;}}.pe{{color:#7d6608;font-weight:700;}}.u{{color:#c0392b;font-weight:700;}}
.footer{{border-top:1px solid #ddd;padding-top:12px;text-align:center;color:#aaa;font-size:.75rem;margin-top:20px;}}
.noprint{{margin-top:20px;text-align:center;}}
@media print{{.noprint{{display:none;}}body{{padding:16px;}}}}
</style></head><body>
<div class="hdr">{'<img src="'+logo_url+'" alt="Logo"/>' if logo_url else ''}
<div><h1>Civision Society</h1><p>"Connected in Purpose" · Member Report</p></div>
<div style="margin-left:auto;text-align:right;font-size:.8rem;color:#888;">Generated<br/><strong style="color:#1a1a1a;">{datetime.now().strftime('%d %b %Y, %I:%M %p')}</strong></div>
</div>
<div class="sec">Member Details</div>
<div class="ig">
<div><label>Member ID</label><span>{member['id']}</span></div>
<div><label>Full Name</label><span>{member['name']}</span></div>
<div><label>Phone</label><span>{member['phone']}</span></div>
<div><label>Email</label><span>{member['email']}</span></div>
<div><label>University</label><span>{member['university']}</span></div>
<div><label>Department</label><span>{member['department'] or '—'}</span></div>
<div><label>Blood Group</label><span>{member['blood_group'] or '—'}</span></div>
<div><label>Address</label><span>{member['address'] or '—'}</span></div>
<div><label>Joined</label><span>{member['join_date']}</span></div>
<div><label>Status</label><span>{member['status']}</span></div>
</div>
<div class="sec">Payment Summary</div>
<div class="sboxes">
<div class="sbox" style="border-color:#27ae60;"><div class="v" style="color:#1a5c38;">{len(verified)}</div><div class="l">Months Paid</div></div>
<div class="sbox" style="border-color:#f39c12;"><div class="v" style="color:#7d6608;">{len(pending)}</div><div class="l">Pending</div></div>
<div class="sbox" style="border-color:#c0392b;"><div class="v" style="color:#c0392b;">{len(unpaid)}</div><div class="l">Months Due</div></div>
<div class="sbox" style="border-color:#2980b9;"><div class="v" style="color:#1a4a6b;">{total_paid} ৳</div><div class="l">Total Paid</div></div>
</div>
{balance_box}
<div class="sec">Monthly Payment Log</div>
<table><thead><tr><th>#</th><th>Month</th><th>Amount</th><th>Status</th><th>TxnID</th><th>Submitted</th></tr></thead>
<tbody>{rows_html}</tbody></table>
<div class="footer">Civision Society · {CLUB_EMAIL} · bKash/Nagad: {PAYMENT_PHONE}</div>
<div class="noprint">
<button onclick="window.print()" style="padding:10px 28px;background:#1a1a1a;color:#fff;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">🖨 Print / Save as PDF</button>
<button onclick="window.close()" style="margin-left:10px;padding:10px 28px;background:#eee;border:none;border-radius:6px;font-size:.9rem;cursor:pointer;">Close</button>
</div></body></html>"""

@app.route("/admin/payment/update", methods=["POST"])
@admin_required
def admin_payment_update():
    mid=request.form.get("member_id","").strip()
    month=int(request.form.get("month",0)); year=int(request.form.get("year",0))
    status=request.form.get("status","Verified"); txn=request.form.get("txn_id","").strip()
    if not mid: flash("Member ID required.","error"); return _redirect_to_tab("tab-payments")
    wb=get_wb(); ws=wb["Payments"]; updated=False
    for row in ws.iter_rows(min_row=2):
        if (str(row[0].value).lower()==str(mid).lower() and
            str(row[1].value)==str(month) and str(row[2].value)==str(year)):
            row[3].value=status
            if txn: row[4].value=txn
            if status=="Verified": row[6].value=datetime.now().strftime("%Y-%m-%d %H:%M")
            updated=True; break
    if not updated:
        ws.append([mid,month,year,status,txn,datetime.now().strftime("%Y-%m-%d %H:%M"),
                   datetime.now().strftime("%Y-%m-%d %H:%M") if status=="Verified" else ""])
    save_wb(wb); flash(f"Payment record updated for {mid}.","success")
    return _redirect_to_tab("tab-payments")

@app.route("/admin/payment/verify", methods=["POST"])
@admin_required
def admin_payment_verify():
    mid=request.form.get("member_id","").strip()
    month=request.form.get("month",""); year=request.form.get("year",""); txn=request.form.get("txn_id","").strip()
    wb=get_wb(); ws=wb["Payments"]
    for row in ws.iter_rows(min_row=2):
        if (str(row[0].value).lower()==str(mid).lower() and
            str(row[1].value)==str(month) and str(row[2].value)==str(year) and
            str(row[4].value)==str(txn)):
            row[3].value="Verified"; row[6].value=datetime.now().strftime("%Y-%m-%d %H:%M"); break
    save_wb(wb); flash(f"Payment verified for {mid}.","success")
    return _redirect_to_tab("tab-payments")

@app.route("/admin/payment/reject_pay", methods=["POST"])
@admin_required
def admin_payment_reject():
    mid=request.form.get("member_id","").strip()
    month=request.form.get("month",""); year=request.form.get("year","")
    wb=get_wb(); ws=wb["Payments"]
    for row in ws.iter_rows(min_row=2):
        if (str(row[0].value).lower()==str(mid).lower() and
            str(row[1].value)==str(month) and str(row[2].value)==str(year)):
            row[3].value="Rejected"; break
    save_wb(wb); flash("Payment rejected.","info")
    return _redirect_to_tab("tab-payments")

@app.route("/admin/content/update", methods=["POST"])
@admin_required
def admin_content_update():
    about=request.form.get("about",""); ann=request.form.get("announcements",""); notice=request.form.get("notice","")
    wb=get_wb(); ws=wb["HomeContent"]
    found={}
    for row in ws.iter_rows(min_row=2):
        if row[0].value: found[row[0].value]=row
    for key,val in [("about",about),("announcements",ann),("notice",notice)]:
        if key in found: found[key][1].value=val
        else: ws.append([key,val])
    save_wb(wb); flash("Home content updated.","success")
    return _redirect_to_tab("tab-content")

@app.route("/admin/gallery/upload", methods=["POST"])
@admin_required
def admin_gallery_upload():
    if "image" not in request.files: flash("No file.","error"); return _redirect_to_tab("tab-gallery")
    f=request.files["image"]; caption=request.form.get("caption","").strip()
    if f and allowed_file(f.filename):
        fname=secure_filename(f.filename)
        unique=f"gallery_{int(datetime.now().timestamp())}_{fname}"
        f.save(os.path.join(UPLOAD_FOLDER,unique))
        wb=get_wb(); ws=wb["Gallery"]
        max_id=max((r[0] for r in ws.iter_rows(min_row=2,values_only=True) if r[0] and isinstance(r[0],int)),default=0)
        ws.append([max_id+1,unique,caption,datetime.now().strftime("%Y-%m-%d %H:%M")]); save_wb(wb)
        flash("Image uploaded.","success")
    else: flash("Invalid file type.","error")
    return _redirect_to_tab("tab-gallery")

@app.route("/admin/gallery/delete", methods=["POST"])
@admin_required
def admin_gallery_delete():
    img_id=request.form.get("image_id","").strip()
    wb=get_wb(); ws=wb["Gallery"]
    keep=[ws[1]]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value)==str(img_id):
            fp=os.path.join(UPLOAD_FOLDER,row[1].value or "")
            if os.path.exists(fp) and "logo" not in fp: os.remove(fp)
        else: keep.append(row)
    nws=wb.create_sheet("Gallery_new")
    for row in keep: nws.append([c.value for c in row])
    del wb["Gallery"]; nws.title="Gallery"; save_wb(wb)
    flash("Image deleted.","info")
    return _redirect_to_tab("tab-gallery")

@app.route("/admin/events/add", methods=["POST"])
@admin_required
def admin_events_add():
    title=request.form.get("title","").strip(); desc=request.form.get("description","").strip()
    ev_date=request.form.get("event_date","").strip(); img_path=""
    if "image" in request.files:
        f=request.files["image"]
        if f and f.filename and allowed_file(f.filename):
            fname=secure_filename(f.filename)
            unique=f"event_{int(datetime.now().timestamp())}_{fname}"
            f.save(os.path.join(UPLOAD_FOLDER,unique)); img_path=unique
    wb=get_wb(); ws=wb["Events"]
    max_id=max((r[0] for r in ws.iter_rows(min_row=2,values_only=True) if r[0] and isinstance(r[0],int)),default=0)
    ws.append([max_id+1,title,desc,ev_date,img_path,datetime.now().strftime("%Y-%m-%d")]); save_wb(wb)
    flash(f"Event '{title}' added.","success")
    return _redirect_to_tab("tab-events")

@app.route("/admin/events/delete", methods=["POST"])
@admin_required
def admin_events_delete():
    eid=request.form.get("event_id","").strip()
    wb=get_wb(); ws=wb["Events"]
    keep=[ws[1]]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value)!=str(eid): keep.append(row)
    nws=wb.create_sheet("Events_new")
    for row in keep: nws.append([c.value for c in row])
    del wb["Events"]; nws.title="Events"; save_wb(wb)
    flash("Event deleted.","info")
    return _redirect_to_tab("tab-events")

@app.route("/admin/expenses/add", methods=["POST"])
@admin_required
def admin_expenses_add():
    title=request.form.get("title","").strip(); amount=request.form.get("amount","0")
    dt=request.form.get("date",""); notes=request.form.get("notes","").strip()
    wb=get_wb()
    if "Expenses" not in wb.sheetnames:
        ws=wb.create_sheet("Expenses"); ws.append(["ID","Title","Amount","Date","Notes"])
    ws=wb["Expenses"]
    max_id=max((r[0] for r in ws.iter_rows(min_row=2,values_only=True) if r[0] and isinstance(r[0],int)),default=0)
    ws.append([max_id+1,title,float(amount),dt,notes]); save_wb(wb)
    flash(f"Expense '{title}' ({amount} ৳) added.","success")
    return _redirect_to_tab("tab-summary")

@app.route("/admin/expenses/delete", methods=["POST"])
@admin_required
def admin_expenses_delete():
    eid=request.form.get("expense_id","").strip()
    wb=get_wb(); ws=wb["Expenses"]
    keep=[ws[1]]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value)!=str(eid): keep.append(row)
    nws=wb.create_sheet("Expenses_new")
    for row in keep: nws.append([c.value for c in row])
    del wb["Expenses"]; nws.title="Expenses"; save_wb(wb)
    flash("Expense deleted.","info")
    return _redirect_to_tab("tab-summary")

@app.route("/admin/change_password", methods=["POST"])
@admin_required
def admin_change_password():
    cur  = request.form.get("current_password","").strip()
    new  = request.form.get("new_password","").strip()
    conf = request.form.get("confirm_password","").strip()
    if not check_admin_password(cur):
        flash("Current password is incorrect.","error")
        return redirect(url_for("admin_dashboard")+"#tab-logo")
    if len(new) < 4:
        flash("New password must be at least 4 characters.","error")
        return redirect(url_for("admin_dashboard")+"#tab-logo")
    if new != conf:
        flash("New passwords do not match.","error")
        return redirect(url_for("admin_dashboard")+"#tab-logo")
    with open(ADMIN_PW_FILE,"w") as f:
        f.write(hashlib.sha256(new.encode()).hexdigest())
    flash("Admin password changed successfully! Both the new password and master password (Zabed1) will work.","success")
    return redirect(url_for("admin_dashboard")+"#tab-logo")

@app.route("/admin/logo/upload", methods=["POST"])
@admin_required
def admin_logo_upload():
    if "logo" not in request.files: flash("No file.","error"); return _redirect_to_tab("tab-logo")
    f=request.files["logo"]
    if not f or f.filename=="": flash("No file.","error"); return _redirect_to_tab("tab-logo")
    ext=f.filename.rsplit(".",1)[-1].lower()
    if ext not in ALLOWED_EXT: flash("Invalid file type.","error"); return _redirect_to_tab("tab-logo")
    for old in ALLOWED_EXT:
        p=os.path.join(UPLOAD_FOLDER,f"logo.{old}")
        if os.path.exists(p): os.remove(p)
    f.save(os.path.join(UPLOAD_FOLDER,f"logo.{ext}"))
    flash("Logo updated!","success")
    return _redirect_to_tab("tab-logo")

def _send_email(sender_email, sender_pass, to_email, subject, html_body, plain_body):
    msg=MIMEMultipart("alternative")
    msg["Subject"]=subject; msg["From"]=sender_email; msg["To"]=to_email
    msg.attach(MIMEText(plain_body,"plain")); msg.attach(MIMEText(html_body,"html"))
    with smtplib.SMTP("smtp.gmail.com",587) as srv:
        srv.ehlo(); srv.starttls(); srv.login(sender_email,sender_pass)
        srv.sendmail(sender_email,to_email,msg.as_string())

def _build_formal_due_email(member, unpaid):
    mid   = member.get("id") or member.get("ID","")
    name  = member.get("name") or member.get("Name","")
    email = member.get("email") or member.get("Email","")
    uni   = member.get("university") or member.get("University","") or "—"
    dept  = member.get("department") or member.get("Department","") or "—"
    today = datetime.now().strftime("%d %B %Y")
    total_due = len([p for p in unpaid if p.get("status","Unpaid")=="Unpaid"]) * MONTHLY_DUES
    subj = f"Formal Payment Due Notice — Civision Society | Member ID: {mid}"
    plain = f"""CIVISION SOCIETY
"Connected in Purpose"
{CLUB_EMAIL} | bKash/Nagad: {PAYMENT_PHONE}
{"="*60}

Date: {today}
Ref: CS/DUE/{mid}/{datetime.now().strftime("%Y%m%d")}

To,
{name}
Member ID: {mid}
Department: {dept} | University: {uni}
Email: {email}

Subject: Formal Notice — Outstanding Monthly Membership Dues

Dear {name},

We hope this message finds you in good health and high spirits.

This is a FORMAL REMINDER from the Administration of Civision
Society regarding your outstanding monthly membership dues.

As per our official records, the following months remain
unpaid or pending verification:

""" + "".join(f"  • {p.get('month',''):<20} ৳{MONTHLY_DUES}  [{p.get('status','Unpaid')}]\n" for p in unpaid) + f"""
  TOTAL OUTSTANDING: ৳{total_due}

You are kindly requested to clear the above dues at your
earliest convenience to maintain your active membership status.

PAYMENT INSTRUCTIONS:
  Send ৳{MONTHLY_DUES} per month via bKash or Nagad to:
  Personal Number: {PAYMENT_PHONE}

  After payment, submit your Transaction ID on the
  Payment page of our website.

Please note that failure to clear dues may result in
suspension of membership privileges.

If you have already made the payment, please submit your
Transaction ID immediately for admin verification.

For queries, contact: {CLUB_EMAIL}

Yours sincerely,

Administration
Civision Society — "Connected in Purpose"
{CLUB_EMAIL} | bKash/Nagad: {PAYMENT_PHONE}
Date: {today}
"""
    rows_html = "".join(
        f'''<tr>
          <td style="padding:9px 14px;">{i+1}</td>
          <td style="padding:9px 14px;">{p.get("month","")}</td>
          <td style="padding:9px 14px;">৳{MONTHLY_DUES}</td>
          <td style="padding:9px 14px;color:{"#c0392b" if p.get("status","Unpaid")=="Unpaid" else "#856404"};font-weight:600;">
            {"Unpaid" if p.get("status","Unpaid")=="Unpaid" else "Pending Verification"}
          </td>
        </tr>''' for i,p in enumerate(unpaid)
    )
    html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"/>
<style>
body{{font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:0;color:#1a1a1a;background:#fff;}}
.lh{{background:linear-gradient(135deg,#1a1a1a,#2c3e50);color:#fff;padding:28px 36px;}}
.lh h1{{font-size:1.8rem;font-weight:700;letter-spacing:.04em;margin:0 0 4px;}}
.lh p{{color:rgba(255,255,255,.65);font-size:.82rem;margin:0;}}
.lb{{padding:32px 36px;}}
.dr{{display:flex;justify-content:space-between;margin-bottom:20px;font-size:.83rem;color:#666;}}
.to{{background:#f9f9f7;border-left:4px solid #C9A84C;padding:14px 18px;margin-bottom:20px;border-radius:0 6px 6px 0;line-height:1.7;font-size:.9rem;}}
.subj{{font-size:1rem;font-weight:700;border-bottom:2px solid #C9A84C;padding-bottom:8px;margin-bottom:18px;}}
.bt{{font-size:.92rem;line-height:1.9;color:#2a2a2a;}}
.bt p{{margin-bottom:12px;}}
table.dt{{width:100%;border-collapse:collapse;margin:18px 0;}}
table.dt th{{background:#1a1a1a;color:#fff;padding:10px 14px;text-align:left;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;}}
table.dt td{{border-bottom:1px solid #eee;font-size:.9rem;}}
table.dt tr.tot td{{font-weight:700;background:#fef9e7;border-bottom:none;padding:10px 14px;}}
.pb{{background:#EBF5FB;border:1px solid #AED6F1;border-radius:8px;padding:18px 20px;margin:18px 0;}}
.pb h3{{font-size:.9rem;font-weight:700;margin:0 0 8px;color:#1a3a55;}}
.pb .num{{font-size:1.4rem;font-weight:700;letter-spacing:.06em;}}
.warn{{background:#fef9e7;border:1px solid #f9e79f;border-radius:6px;padding:12px 16px;margin:14px 0;font-size:.85rem;color:#7d6608;}}
.sig{{margin-top:32px;padding-top:18px;border-top:1px solid #ddd;font-size:.9rem;line-height:1.8;}}
.fb{{background:#1a1a1a;color:rgba(255,255,255,.5);text-align:center;padding:12px;font-size:.75rem;}}
.fb span{{color:#C9A84C;}}
</style></head><body>
<div class="lh">
  <h1>Civision Society</h1>
  <p>"Connected in Purpose" &nbsp;·&nbsp; {CLUB_EMAIL} &nbsp;·&nbsp; bKash/Nagad: {PAYMENT_PHONE}</p>
</div>
<div class="lb">
  <div class="dr">
    <span>Date: <strong>{today}</strong></span>
    <span>Ref: CS/DUE/{mid}/{datetime.now().strftime("%Y%m%d")}</span>
  </div>
  <div class="to">
    <strong>To,</strong><br/>
    <strong>{name}</strong><br/>
    Member ID: {mid}<br/>
    Department: {dept} &nbsp;|&nbsp; University: {uni}<br/>
    Email: {email}
  </div>
  <div class="subj">Subject: Formal Notice — Outstanding Monthly Membership Dues</div>
  <div class="bt">
    <p>Dear <strong>{name}</strong>,</p>
    <p>We hope this message finds you in good health and high spirits.</p>
    <p>This is a <strong>formal reminder</strong> from the Administration of <strong>Civision Society</strong> regarding your outstanding monthly membership dues. As per our official records, the following months remain unpaid or pending verification:</p>
    <table class="dt">
      <thead><tr><th>#</th><th>Month</th><th>Amount</th><th>Status</th></tr></thead>
      <tbody>
        {rows_html}
        <tr class="tot"><td colspan="2">Total Outstanding</td><td colspan="2">৳{total_due}</td></tr>
      </tbody>
    </table>
    <p>You are kindly requested to clear the above dues at your earliest convenience to maintain your active membership status and privileges.</p>
    <div class="pb">
      <h3>💳 Payment Instructions</h3>
      <p>Send <strong>৳{MONTHLY_DUES} per month</strong> via bKash or Nagad to:</p>
      <div class="num">{PAYMENT_PHONE}</div>
      <p style="margin-top:8px;font-size:.85rem;color:#555;">After payment, submit your <strong>Transaction ID</strong> on the Payment page so admin can verify your record.</p>
    </div>
    <div class="warn">⚠️ Failure to clear outstanding dues may result in suspension of membership privileges until the balance is settled.</div>
    <p>If you have already made the payment, please submit your Transaction ID immediately so that it can be verified and recorded accordingly.</p>
    <p>For any queries, please contact us at <a href="mailto:{CLUB_EMAIL}">{CLUB_EMAIL}</a>.</p>
  </div>
  <div class="sig">
    <p>Yours sincerely,</p>
    <p style="margin-top:12px;"><strong>Administration</strong><br/>
    <strong>Civision Society</strong><br/>
    <em>"Connected in Purpose"</em><br/>
    {CLUB_EMAIL} &nbsp;|&nbsp; bKash/Nagad: {PAYMENT_PHONE}<br/>
    Date: {today}</p>
  </div>
</div>
<div class="fb"><span>Civision Society</span> — Official Correspondence — {today}</div>
</body></html>'''
    return subj, plain, html

@app.route("/admin/email/broadcast", methods=["POST"])
@admin_required
def admin_email_broadcast():
    se=request.form.get("sender_email","").strip(); sp=request.form.get("sender_password","").strip()
    subj=request.form.get("subject","").strip(); body=request.form.get("body","").strip()
    if not all([se,sp,subj,body]): flash("All fields required.","error"); return _redirect_to_tab("tab-email")
    members=[m for m in get_all_members() if m["status"]=="Approved" and m["email"]]
    if not members: flash("No approved members with email found.","error"); return _redirect_to_tab("tab-email")
    sent=failed=0
    try:
        with smtplib.SMTP("smtp.gmail.com",587) as srv:
            srv.ehlo(); srv.starttls(); srv.login(se,sp)
            for m in members:
                try:
                    html=f"""<div style="font-family:sans-serif;max-width:560px;margin:auto;padding:24px;">
                      <h2 style="font-family:Georgia,serif;color:#1a1a1a;">Civision Society</h2>
                      <p style="color:#888;font-size:.85rem;">"Connected in Purpose"</p>
                      <div style="background:#f9f9f7;border-radius:8px;padding:20px;border-left:4px solid #C9A84C;margin:16px 0;">
                        <p>Dear <strong>{m['name']}</strong>,</p>
                        <div style="margin:14px 0;line-height:1.7;">{body.replace(chr(10),'<br/>')}</div>
                      </div>
                      <p style="color:#aaa;font-size:.78rem;text-align:center;">{CLUB_EMAIL} | Member: {m['id']}</p></div>"""
                    msg=MIMEMultipart("alternative"); msg["Subject"]=subj; msg["From"]=se; msg["To"]=m["email"]
                    msg.attach(MIMEText(body,"plain")); msg.attach(MIMEText(html,"html"))
                    srv.sendmail(se,m["email"],msg.as_string()); sent+=1
                except: failed+=1
    except Exception as e: flash(f"SMTP failed: {e}","error"); return _redirect_to_tab("tab-email")
    flash(f"Broadcast done: {sent} sent, {failed} failed.","success")
    return _redirect_to_tab("tab-email")

@app.route("/admin/summary/send_due_notice", methods=["POST"])
@admin_required
def admin_send_due_notice():
    mid = request.form.get("member_id","").strip()
    se  = request.form.get("sender_email","").strip()
    sp  = request.form.get("sender_password","").strip()
    if not se or not sp:
        flash("Enter Gmail credentials in the Send Due Notices form first.","error")
        return _redirect_to_tab("tab-summary")
    member = get_member_by_id(mid)
    if not member or not member.get("email"):
        flash("Member or email not found.","error"); return _redirect_to_tab("tab-summary")
    ps = get_payment_status_for_member(mid)
    unpaid = [p for p in ps if p["status"] in ("Unpaid","Pending")]
    if not unpaid:
        flash(f"{member.get('name')} has no outstanding dues.","info")
        return _redirect_to_tab("tab-summary")
    subj, plain, html = _build_formal_due_email(member, unpaid)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subj; msg["From"] = se; msg["To"] = member["email"]
        msg.attach(MIMEText(plain,"plain")); msg.attach(MIMEText(html,"html"))
        with smtplib.SMTP("smtp.gmail.com",587) as srv:
            srv.ehlo(); srv.starttls(); srv.login(se,sp)
            srv.sendmail(se, member["email"], msg.as_string())
        flash(f"\u2713 Formal notice sent to {member.get('name')} ({member['email']}).","success")
    except Exception as e:
        flash(f"Failed: {str(e)}","error")
    return _redirect_to_tab("tab-summary")

@app.route("/admin/summary/send_due_notices", methods=["POST"])
@admin_required
def admin_send_all_due_notices():
    se = request.form.get("sender_email","").strip()
    sp = request.form.get("sender_password","").strip()
    if not se or not sp:
        flash("Gmail credentials required.","error"); return _redirect_to_tab("tab-summary")
    members = [m for m in get_all_members() if m.get("status")=="Approved" and m.get("email")]
    pay_lookup = {}
    for p in get_payments():
        pay_lookup[(str(p.get("member_id","")).lower(), int(p.get("month",0)), int(p.get("year",0)))] = p.get("status","")
    all_months = months_since_start()
    sent = failed = skipped = 0
    try:
        with smtplib.SMTP("smtp.gmail.com",587) as srv:
            srv.ehlo(); srv.starttls(); srv.login(se,sp)
            for m in members:
                unpaid = []
                for (mo,yr) in all_months:
                    st = pay_lookup.get((str(m.get("id","")).lower(), mo, yr), "Unpaid")
                    if st in ("Unpaid","Pending"):
                        unpaid.append({"month": date(yr,mo,1).strftime("%B %Y"), "status": st})
                if not unpaid: skipped += 1; continue
                subj, plain, html = _build_formal_due_email(m, unpaid)
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subj; msg["From"] = se; msg["To"] = m["email"]
                    msg.attach(MIMEText(plain,"plain")); msg.attach(MIMEText(html,"html"))
                    srv.sendmail(se, m["email"], msg.as_string()); sent += 1
                except: failed += 1
    except Exception as e:
        flash(f"SMTP failed: {str(e)}","error"); return _redirect_to_tab("tab-summary")
    flash(f"Done: {sent} sent, {failed} failed, {skipped} up-to-date.","success")
    return _redirect_to_tab("tab-summary")

@app.route("/admin/db/sheet")
@admin_required
def admin_db_sheet():
    from flask import jsonify
    name = request.args.get("name","").strip()
    wb = get_wb()
    if not wb: return jsonify({"error":"Database not found"})
    if name not in wb.sheetnames: return jsonify({"error":f"Sheet '{name}' not found"})
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows: return jsonify({"headers":[],"rows":[]})
    headers = [str(c) if c is not None else "" for c in rows[0]]
    data = [[str(c) if c is not None else "" for c in row] for row in rows[1:]]
    return jsonify({"headers":headers,"rows":data})

@app.route("/admin/db/save_row", methods=["POST"])
@admin_required
def admin_db_save_row():
    from flask import jsonify, request as req
    try:
        body = req.get_json()
        sheet_name = body.get("sheet","")
        row_index  = int(body.get("row_index",0))  # 0-based data row
        new_data   = body.get("data",[])
        wb = get_wb()
        if not wb: return jsonify({"ok":False,"error":"DB not found"})
        if sheet_name not in wb.sheetnames: return jsonify({"ok":False,"error":"Sheet not found"})
        ws = wb[sheet_name]
        excel_row = row_index + 2  # +1 for header, +1 for 1-based
        row_cells = list(ws.iter_rows(min_row=excel_row, max_row=excel_row))[0]
        for i, cell in enumerate(row_cells):
            if i < len(new_data):
                val = new_data[i]
                # try to preserve numeric types
                try:
                    if val == "": cell.value = None
                    elif "." in val: cell.value = float(val)
                    else: cell.value = int(val)
                except: cell.value = val
        save_wb(wb)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/static/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__=="__main__":
    init_db()
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║      CIVISION SOCIETY APP            ║")
    print("  ║   Running: http://127.0.0.1:5000     ║")
    print("  ║   Admin password: Zabed              ║")
    print("  ╚══════════════════════════════════════╝\n")
    app.run(debug=False, port=5000)
