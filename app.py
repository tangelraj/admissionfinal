import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient

# Load .env
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret')

# Database (SQLite)
db_url = os.getenv('DATABASE_URL', 'sqlite:///admissions.db')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Mail config
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 25))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'false').lower() in ('true', '1', 'yes')
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

# Twilio config (optional)
TWILIO_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM = os.getenv('TWILIO_FROM')
twilio_client = None
if TWILIO_SID and TWILIO_AUTH:
    try:
        twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
    except Exception:
        twilio_client = None

# Simple Admin creds (for demo). For production use a proper auth system.
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'adminpass')

# Models
class Applicant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    course = db.Column(db.String(100), nullable=True)
    address = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending / approved / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    admin_note = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<Applicant {self.id} {self.full_name} ({self.status})>'

# Helpers: send email
def send_email(subject, recipient, html_body):
    if not app.config.get('MAIL_SERVER'):
        app.logger.warning("Mail server not configured — skipping email.")
        return False
    try:
        msg = Message(subject=subject, recipients=[recipient], html=html_body,
                      sender=app.config.get('MAIL_USERNAME'))
        mail.send(msg)
        app.logger.info(f"Email sent to {recipient}")
        return True
    except Exception as e:
        app.logger.exception("Failed to send email: %s", e)
        return False

# Helpers: send sms (twilio)
def send_sms(to_number, body):
    if not twilio_client or not TWILIO_FROM:
        app.logger.warning("Twilio not configured — skipping SMS.")
        return False
    try:
        message = twilio_client.messages.create(body=body, from_=TWILIO_FROM, to=to_number)
        app.logger.info("SMS sent SID: %s", message.sid)
        return True
    except Exception as e:
        app.logger.exception("Failed to send SMS: %s", e)
        return False

# Routes
@app.route('/')
def index():
    return redirect(url_for('register'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        course = request.form.get('course')
        address = request.form.get('address')

        if not full_name or not email:
            flash('Name and email are required.', 'danger')
            return redirect(url_for('register'))

        applicant = Applicant(
            full_name=full_name.strip(),
            email=email.strip(),
            phone=phone.strip() if phone else None,
            course=course.strip() if course else None,
            address=address.strip() if address else None
        )
        db.session.add(applicant)
        db.session.commit()

        # Send confirmation email (applicant)
        html = render_template('email_template.html', applicant=applicant, action='received')
        send_email(f'Application received — {applicant.full_name}', applicant.email, html)

        # Optionally send SMS
        if applicant.phone:
            sms_body = f"Hi {applicant.full_name}, we received your application. ID: {applicant.id}"
            send_sms(applicant.phone, sms_body)

        return render_template('register_success.html', applicant=applicant)

    return render_template('register.html')

# Admin login (very basic)
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Logged in as admin.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials.', 'danger')
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Logged out.', 'info')
    return redirect(url_for('admin_login'))

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please log in as admin.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin')
@admin_required
def admin_dashboard():
    total = Applicant.query.count()
    pending = Applicant.query.filter_by(status='pending').count()
    approved = Applicant.query.filter_by(status='approved').count()
    rejected = Applicant.query.filter_by(status='rejected').count()
    return render_template('admin_dashboard.html', total=total, pending=pending, approved=approved, rejected=rejected)

@app.route('/admin/pending')
@admin_required
def admin_pending_list():
    applicants = Applicant.query.filter_by(status='pending').order_by(Applicant.created_at.asc()).all()
    return render_template('admin_pending_list.html', applicants=applicants)

@app.route('/admin/approve/<int:applicant_id>', methods=['POST'])
@admin_required
def approve_applicant(applicant_id):
    a = Applicant.query.get_or_404(applicant_id)
    a.status = 'approved'
    a.admin_note = request.form.get('admin_note', '')
    db.session.commit()

    # send approval email
    html = render_template('email_template.html', applicant=a, action='approved')
    send_email(f'Application approved — {a.full_name}', a.email, html)

    # send SMS
    if a.phone:
        send_sms(a.phone, f"Congratulations {a.full_name}! Your application (ID:{a.id}) is approved.")

    flash(f'Applicant {a.full_name} approved.', 'success')
    return redirect(url_for('admin_pending_list'))

@app.route('/admin/reject/<int:applicant_id>', methods=['POST'])
@admin_required
def reject_applicant(applicant_id):
    a = Applicant.query.get_or_404(applicant_id)
    a.status = 'rejected'
    a.admin_note = request.form.get('admin_note', '')
    db.session.commit()

    # send rejection email
    html = render_template('email_template.html', applicant=a, action='rejected')
    send_email(f'Application update — {a.full_name}', a.email, html)

    if a.phone:
        send_sms(a.phone, f"Hello {a.full_name}, your application (ID:{a.id}) status: rejected.")

    flash(f'Applicant {a.full_name} rejected.', 'info')
    return redirect(url_for('admin_pending_list'))

@app.route('/admin/all')
@admin_required
def admin_all():
    applicants = Applicant.query.order_by(Applicant.created_at.desc()).all()
    return render_template('admin_pending_list.html', applicants=applicants, show_all=True)

with app.app_context():
    db.create_all()


if __name__ == '__main__':
    app.run(debug=True)
