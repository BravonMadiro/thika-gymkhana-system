from flask import (
    Flask, render_template, request, redirect, url_for, Response, flash,
    get_flashed_messages, session, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, or_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import os
import csv
import io
import math
import random
import re
import secrets
import string
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.sax.saxutils import escape
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

# FIX 1: Corrected Flask initialization dunder
app = Flask(__name__)

configured_secret_key = os.environ.get('FLASK_SECRET_KEY')
if os.environ.get('APP_ENV', 'development').lower() == 'production' and not configured_secret_key:
    raise RuntimeError("FLASK_SECRET_KEY must be configured in production.")

# FIX 2: Added a fallback static key for development to prevent session invalidation on restart
app.secret_key = configured_secret_key or os.environ.get('DEV_SECRET_KEY', 'super-secret-dev-key-change-in-prod')

# Configure SQLite Database
os.makedirs('instance', exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:///club.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
db = SQLAlchemy(app)

# FIX 3: Moved uploads out of the public 'static' folder to prevent unauthorized access
UPLOAD_FOLDER = os.path.join(app.instance_path, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

PAYBILL_NUMBER = "123456"
MPESA_PAYMENT_METHODS = {'M-Pesa', 'M-Pesa Paybill'}

def display_payment_method(payment_method):
    return 'M-Pesa' if payment_method in MPESA_PAYMENT_METHODS else payment_method

# --- DATABASE MODELS ---
class User(db.Model):
    __tablename__ = 'users'
    username = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='cashier')

class Service(db.Model):
    __tablename__ = 'services'
    name = db.Column(db.String(100), primary_key=True)
    price = db.Column(db.Float, nullable=False)

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.String(32), primary_key=True)
    date = db.Column(db.String(30), nullable=False)
    customer_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    pax = db.Column(db.Integer, nullable=False, default=1)
    amount = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pending')
    payment_method = db.Column(db.String(30), nullable=False, default='Unpaid')
    reference = db.Column(db.String(50), nullable=False, default='Pending')
    created_by = db.Column(db.String(64), nullable=True)
    confirmed_by = db.Column(db.String(64), nullable=True)

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.String(32), primary_key=True)
    doc_type = db.Column(db.String(30), nullable=False, default='LPO')
    date_created = db.Column(db.String(30), nullable=False)
    caretaker_name = db.Column(db.String(100), nullable=False)
    item_description = db.Column(db.Text, nullable=False)
    estimated_amount = db.Column(db.String(32), nullable=False, default='0.00')
    actual_amount = db.Column(db.String(32), nullable=False, default='0.00')
    invoice_ref = db.Column(db.String(50), nullable=False, default='Pending')
    receipt_file = db.Column(db.String(255), nullable=False, default='N/A')
    receipt_note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='Pending Purchase')
    logged_by = db.Column(db.String(64), nullable=True)
    fulfilled_by = db.Column(db.String(64), nullable=True)
    last_payment_date = db.Column(db.String(30), nullable=True)
    last_payment_by = db.Column(db.String(64), nullable=True)
    variance_type = db.Column(db.String(50), nullable=True)
    variance_amount = db.Column(db.String(32), nullable=True)
    audit_ref = db.Column(db.String(50), nullable=True)
    linked_transfer_id = db.Column(db.String(32), nullable=True)
    linked_asset_tracking_id = db.Column(db.Integer, nullable=True)
    approved_by = db.Column(db.String(64), nullable=True)
    approved_at = db.Column(db.String(30), nullable=True)
    rejected_by = db.Column(db.String(64), nullable=True)
    rejection_reason = db.Column(db.String(500), nullable=True)

class PaymentHistory(db.Model):
    __tablename__ = 'payment_history'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    expense_id = db.Column(db.String(32), db.ForeignKey('expenses.id'), nullable=False)
    amount = db.Column(db.String(32), nullable=False)
    date = db.Column(db.String(30), nullable=False)
    paid_by = db.Column(db.String(64), nullable=True)
    invoice_ref = db.Column(db.String(50), nullable=True)
    receipt_file = db.Column(db.String(255), nullable=True)
    receipt_note = db.Column(db.String(500), nullable=True)
    variance_reason = db.Column(db.String(500), nullable=True)

class AssetTransfer(db.Model):
    __tablename__ = 'asset_transfers'
    id = db.Column(db.String(32), primary_key=True)
    date_sent = db.Column(db.String(30), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    requested_by = db.Column(db.String(100), nullable=False)
    approved_by = db.Column(db.String(64), nullable=True)
    custodian = db.Column(db.String(100), nullable=False)
    return_date = db.Column(db.String(30), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='Active')
    discrepancy_notes = db.Column(db.Text, nullable=True, default='N/A')
    recovery_bill_id = db.Column(db.String(32), nullable=True)
    reconciled_by = db.Column(db.String(64), nullable=True)

class TransferItem(db.Model):
    __tablename__ = 'transfer_items'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    transfer_id = db.Column(db.String(32), db.ForeignKey('asset_transfers.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    sent = db.Column(db.Integer, nullable=False, default=1)
    returned_good = db.Column(db.Integer, nullable=False, default=0)
    damaged = db.Column(db.Integer, nullable=False, default=0)
    missing = db.Column(db.Integer, nullable=False, default=0)
    inventory_item = db.relationship('InventoryItem')

class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    def __repr__(self):
        return f"<Department {self.name}>"

class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    category = db.Column(db.String(50), nullable=False, default='Consumables')
    subcategory = db.Column(db.String(60), nullable=False, default='Other')
    unit_of_measure = db.Column(db.String(30), nullable=False, default='Units')
    current_stock = db.Column(db.Integer, nullable=False, default=0)
    reorder_level = db.Column(db.Integer, nullable=False, default=10)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    is_consumable = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    department = db.relationship('Department', backref='inventory_items')
    def __repr__(self):
        item_type = 'Consumable' if self.is_consumable else 'Asset'
        return f"<InventoryItem {self.name} ({item_type})>"

class StoreRequisition(db.Model):
    __tablename__ = 'store_requisitions'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    requester_name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(30), default='PENDING')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_by = db.Column(db.String(64), nullable=True)
    issued_by = db.Column(db.String(64), nullable=True)
    rejected_by = db.Column(db.String(64), nullable=True)
    rejection_reason = db.Column(db.String(500), nullable=True)
    item = db.relationship('InventoryItem')
    department = db.relationship('Department')

class AssetTracking(db.Model):
    __tablename__ = 'asset_tracking'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=False)
    borrower_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    borrowed_at = db.Column(db.DateTime, default=datetime.utcnow)
    returned_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='OUT')
    inspection_notes = db.Column(db.Text, nullable=True)
    recovery_triggered = db.Column(db.Boolean, default=False)
    issued_by = db.Column(db.String(64), nullable=True)
    returned_by = db.Column(db.String(64), nullable=True)
    recovery_expense_id = db.Column(db.String(32), nullable=True, unique=True)
    item = db.relationship('InventoryItem')
    department = db.relationship('Department')

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(64), nullable=True)
    item = db.relationship('InventoryItem', backref='movements')
    department = db.relationship('Department')

INVENTORY_CATEGORIES = {
    'Housekeeping': ('Cleaning Supplies', 'Paper & Tissue', 'Laundry & Linen', 'Waste & Hygiene', 'Other'),
    'Food & Beverage': ('Bar Beverages', 'Kitchen Supplies', 'Food Ingredients', 'Service Disposables', 'Other'),
    'Maintenance & Engineering': ('Electrical', 'Plumbing', 'Building & Carpentry', 'Paint & Hardware', 'Tools', 'Other'),
    'Events & Banqueting': ('Tableware', 'Event Furniture', 'Decor', 'Audio-Visual Equipment', 'Other'),
    'Sports & Recreation': ('Sports Equipment', 'Pool Supplies', 'Grounds & Landscaping', 'Other'),
    'Office & Administration': ('Stationery', 'Printing', 'IT Accessories', 'Other'),
    'Safety & Security': ('Personal Protective Equipment', 'First Aid', 'Fire Safety', 'Security Equipment', 'Other'),
    'Furniture & Appliances': ('Furniture', 'Kitchen Appliances', 'Other Equipment', 'Other')
}

class Staff(db.Model):
    __tablename__ = 'staff'
    id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(100), nullable=False)

class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.String(20), nullable=False)
    staff_id = db.Column(db.String(32), db.ForeignKey('staff.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='PRESENT')
    hours_worked = db.Column(db.Float, nullable=False, default=8.0)
    is_off_day_work = db.Column(db.Boolean, nullable=False, default=False)
    is_holiday_work = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, nullable=True)

# --- DATABASE INITIALIZATION & SEEDING ---
def init_db():
    with app.app_context():
        db.create_all()
        migrations = {
            'inventory_items': {'is_consumable': 'BOOLEAN NOT NULL DEFAULT TRUE', 'subcategory': "VARCHAR(60) NOT NULL DEFAULT 'Other'"},
            'payment_history': {'invoice_ref': 'VARCHAR(50)', 'receipt_file': 'VARCHAR(255)', 'receipt_note': 'VARCHAR(500)', 'variance_reason': 'VARCHAR(500)'},
            'store_requisitions': {'submitted_by': 'VARCHAR(64)', 'issued_by': 'VARCHAR(64)', 'rejected_by': 'VARCHAR(64)', 'rejection_reason': 'VARCHAR(500)'},
            'asset_tracking': {'issued_by': 'VARCHAR(64)', 'returned_by': 'VARCHAR(64)', 'recovery_expense_id': 'VARCHAR(32)'},
            'asset_transfers': {'reconciled_by': 'VARCHAR(64)'},
            'transfer_items': {'item_id': 'INTEGER'},
            'expenses': {'linked_asset_tracking_id': 'INTEGER', 'approved_by': 'VARCHAR(64)', 'approved_at': 'VARCHAR(30)', 'rejected_by': 'VARCHAR(64)', 'rejection_reason': 'VARCHAR(500)'},
            'stock_movements': {'created_by': 'VARCHAR(64)'}
        }
        with db.engine.begin() as connection:
            for table, columns in migrations.items():
                existing_columns = {column['name'] for column in inspect(connection).get_columns(table)}
                for column, definition in columns.items():
                    if column not in existing_columns:
                        connection.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            
            duplicate_attendance = connection.exec_driver_sql(
                'SELECT 1 FROM attendance GROUP BY date, staff_id HAVING COUNT(*) > 1 LIMIT 1'
            ).first()
            if not duplicate_attendance:
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_date_staff ON attendance (date, staff_id)'
                )

        if not Service.query.first():
            defaults = {'Temporary Guest Fee': 200.0, 'Swimming Fee': 200.0, 'Sports Fee': 150.0, 'Hiring Fee': 300.0}
            for name, price in defaults.items():
                db.session.add(Service(name=name, price=price))
            db.session.commit()

        if not User.query.first():
            initial_password = os.environ.get('INITIAL_ADMIN_PASSWORD')
            if initial_password:
                if len(initial_password) < 8:
                    raise RuntimeError("INITIAL_ADMIN_PASSWORD must be at least 8 characters.")
                username = os.environ.get('INITIAL_ADMIN_USERNAME', 'admin').strip().lower()
                name = os.environ.get('INITIAL_ADMIN_NAME', 'Administrator').strip()
                if not username or not name:
                    raise RuntimeError("INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_NAME cannot be empty.")
                admin_user = User(username=username, name=name, password_hash=generate_password_hash(initial_password), role='admin')
                db.session.add(admin_user)
                db.session.commit()

init_db()

# --- HELPER FUNCTIONS ---
def generate_prefixed_id(doc_type, category_name=None, existing_records=None):
    doc_type_upper = doc_type.upper().strip()
    if doc_type_upper in {'RECONCILIATION', 'REC'}: prefix = 'REC'
    elif doc_type_upper in {'REQUISITION', 'BIL', 'BILL_OF_REQUISITION'}: prefix = 'BIL'
    elif doc_type_upper in {'TRANSFER', 'TRF'}: prefix = 'TRF'
    elif doc_type_upper == 'LPO': prefix = 'LPO'
    elif doc_type_upper == 'SERVICE_BILL' and category_name:
        words = category_name.strip().split()
        prefix = ''.join(word[0] for word in words).upper() if words else 'BIL'
    else: prefix = doc_type_upper[:3]
    
    while True:
        # FIX 4: Increased to 8 digits to prevent infinite loops/collisions
        random_digits = ''.join(random.choices(string.digits, k=8))
        full_code = f"{prefix}{random_digits}"
        exists = False
        if prefix in {'LPO', 'BIL', 'REC'}: exists = Expense.query.get(full_code) is not None
        elif prefix == 'TRF': exists = AssetTransfer.query.get(full_code) is not None
        else: exists = Transaction.query.get(full_code) is not None
        if not exists: return full_code

def fmt_kes(val):
    try:
        amount = Decimal(str(val))
        return f"{amount:,.2f}" if amount.is_finite() else "0.00"
    except (InvalidOperation, ValueError, TypeError): return "0.00"

app.jinja_env.filters['fmt_kes'] = fmt_kes
app.jinja_env.filters['kes'] = fmt_kes

@app.template_filter('comma')
def format_comma(value):
    try:
        if value is None or value == '': return "0.00"
        amount = Decimal(str(value))
        return f"{amount:,.2f}" if amount.is_finite() else "0.00"
    except (InvalidOperation, ValueError, TypeError): return str(value)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def spreadsheet_safe_cell(value):
    text = '' if value is None else str(value)
    if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) or text.lstrip().startswith(('=', '+', '-', '@')):
        return f"'{text}"
    return text

class TemplateRecord(dict):
    def __getattr__(self, name): return self.get(name)

def is_valid_iso_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''): return False
    try: datetime.strptime(value, '%Y-%m-%d')
    except ValueError: return False
    return True

def get_services(): return {s.name: s.price for s in Service.query.all()}

# FIX 5: Removed password_hash from UI helper to prevent security leaks
def get_users():
    return {
        u.username: {'name': u.name, 'role': u.role}
        for u in User.query.all()
    }

def get_expenses(): return Expense.query.order_by(Expense.date_created.desc()).all()
def get_transfers(): return AssetTransfer.query.order_by(AssetTransfer.date_sent.desc()).all()

def get_transfer_items(transfer):
    items = TransferItem.query.filter_by(transfer_id=transfer.id).all()
    if items: return [{'name': i.name, 'sent': i.sent, 'returned_good': i.returned_good, 'damaged': i.damaged, 'missing': i.missing} for i in items]
    return []

# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    # FIX 6: Handled Bootstrap POST logic to prevent permanent lockout
    if User.query.count() == 0:
        if request.method == 'POST':
            username = request.form.get('username', '').strip().lower()
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '')
            if username and name and len(password) >= 8:
                new_u = User(username=username, name=name, password_hash=generate_password_hash(password), role='admin')
                db.session.add(new_u)
                db.session.commit()
                session['user'] = username
                session['role'] = 'admin'
                session['display_name'] = name
                flash("Administrator account created successfully!", "success")
                return redirect(url_for('index'))
            else:
                flash("Please provide a valid username, name, and a password of at least 8 characters.", "error")
        return render_template('login.html', bootstrap_required=True)

    if request.method == 'POST':
        credentials = request.get_json(silent=True) or request.form
        username = credentials.get('username', '').strip().lower()
        password = credentials.get('password', '')
        user_rec = User.query.get(username)
        if user_rec and check_password_hash(user_rec.password_hash, password):
            session['user'] = username
            session['role'] = user_rec.role
            session['display_name'] = user_rec.name or username.capitalize()
            if request.is_json:
                return {'authenticated': True, 'user': username, 'display_name': session['display_name'], 'role': user_rec.role}
            flash(f"Welcome back, {session['display_name']}!", "success")
            return redirect(url_for('index'))
        else:
            if request.is_json: return {'error': 'Invalid username or password.'}, 401
            flash("❌ Invalid username or password.", "error")
    return render_template('react_app.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out securely.", "success")
    return redirect(url_for('login'))

# --- MAIN POS & BILLING ROUTES ---
@app.route('/')
def index():
    if 'user' not in session: return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')): return render_template('react_app.html')
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    selected_date = request.args.get('date', today_str)
    search_query = request.args.get('search', '').strip().lower()
    selected_cashier = request.args.get('cashier_filter', 'all').lower()
    selected_status = request.args.get('status_filter', 'all').lower()
    
    query = Transaction.query
    if selected_date: query = query.filter(Transaction.date.startswith(selected_date))
    transactions = query.all()
    
    filtered_transactions = []
    for t in transactions:
        matches_search = (not search_query or search_query in t.customer_name.lower() or search_query in t.id.lower() or search_query in t.reference.lower())
        creator = (t.created_by or '').lower()
        confirmer = (t.confirmed_by or '').lower()
        matches_cashier = (selected_cashier == 'all') or (selected_cashier in [creator, confirmer])
        matches_status = (selected_status == 'all') or (selected_status == t.status.lower())
        if matches_search and matches_cashier and matches_status:
            filtered_transactions.append({
                'id': t.id, 'date': t.date, 'customer_name': t.customer_name, 'category': t.category,
                'pax': t.pax, 'amount': t.amount, 'status': t.status, 'payment_method': display_payment_method(t.payment_method),
                'reference': t.reference, 'created_by': t.created_by, 'confirmed_by': t.confirmed_by
            })
            
    date_filtered_txs = Transaction.query.filter(Transaction.date.startswith(selected_date)).all()
    paid_txs = [t for t in date_filtered_txs if t.status == 'Paid']
    mpesa_revenue = sum((Decimal(t.amount) for t in paid_txs if t.payment_method in MPESA_PAYMENT_METHODS), Decimal('0.00'))
    cash_revenue = sum((Decimal(t.amount) for t in paid_txs if t.payment_method == 'Cash'), Decimal('0.00'))
    other_revenue = sum((Decimal(t.amount) for t in paid_txs if t.payment_method not in MPESA_PAYMENT_METHODS | {'Cash'}), Decimal('0.00'))
    total_revenue = mpesa_revenue + cash_revenue + other_revenue
    
    services = get_services()
    categories = {cat: Decimal('0.00') for cat in services.keys()}
    for t in paid_txs:
        if t.category in categories: categories[t.category] += Decimal(t.amount)
        
    return render_template('index.html', transactions=filtered_transactions, total_revenue=total_revenue, 
                           mpesa_revenue=mpesa_revenue, cash_revenue=cash_revenue, other_revenue=other_revenue,
                           categories=categories, fee_rates=services, users=get_users(), selected_date=selected_date,
                           search_query=search_query, selected_cashier=selected_cashier, selected_status=selected_status,
                           user=session.get('user'), role=session.get('role'))

@app.route('/confirm_payment/<tx_id>', methods=['POST'])
def confirm_payment(tx_id):
    if 'user' not in session: return redirect(url_for('login'))
    tx = Transaction.query.get(tx_id)
    if not tx: return "Bill not found", 404
    current_date_prefix = tx.date[:10]
    if tx.status != 'Pending':
        flash(f"❌ Bill {tx_id} is not pending.", "error")
        return redirect(url_for('index', date=current_date_prefix))
    try: bill_amount = Decimal(tx.amount)
    except InvalidOperation: bill_amount = Decimal('NaN')
    if not bill_amount.is_finite() or bill_amount <= 0:
        flash("❌ This bill has an invalid amount and cannot be paid.", "error")
        return redirect(url_for('index', date=current_date_prefix))
        
    payment_method = request.form.get('payment_method', 'M-Pesa')
    ref_code = request.form.get('reference', '').strip().upper()
    if payment_method not in {'Cash', 'M-Pesa'}:
        flash("❌ Choose Cash or M-Pesa.", "error")
        return redirect(url_for('index', date=current_date_prefix))
        
    if payment_method == 'Cash':
        # FIX 7: Prevented OOM crash by querying DB directly instead of loading all refs into memory
        while True:
            ref_code = f"CASH{secrets.randbelow(90000000) + 10000000}"
            if not Transaction.query.filter_by(reference=ref_code).first():
                break
    else:
        if not ref_code or len(ref_code) != 10 or not ref_code.isalnum():
            flash("⚠️ Valid 10-character alphanumeric M-Pesa reference required.", "error")
            return redirect(url_for('index', date=current_date_prefix))
        existing_ref = Transaction.query.filter_by(status='Paid', reference=ref_code).first()
        if existing_ref:
            flash(f"🚨 Duplicate M-Pesa Code '{ref_code}' already used!", "error")
            return redirect(url_for('index', date=current_date_prefix))
            
    tx.status = 'Paid'
    tx.payment_method = payment_method
    tx.reference = ref_code
    tx.confirmed_by = session.get('user')
    tx.date = datetime.now().strftime('%Y-%m-%d %H:%M')
    db.session.commit()
    return redirect(url_for('index', date=tx.date[:10]))

# --- ADMIN PANEL & USER MANAGEMENT ---
@app.route('/admin')
def admin_panel():
    if 'user' not in session or session.get('role') != 'admin':
        flash("⚠️ Access denied.", "error")
        return redirect(url_for('index'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')): return render_template('react_app.html')
    return render_template('admin.html', users=get_users(), user=session.get('user'), role=session.get('role'))

# FIX 8: Removed spaces from URL routing rules
@app.route('/admin/users/<username>/delete', methods=['POST'])
def delete_user(username):
    if 'user' not in session or session.get('role') != 'admin': return redirect(url_for('login'))
    if username == session.get('user'):
        flash("You cannot delete your own account.", "error")
    else:
        u = User.query.get(username)
        if u:
            if u.role == 'admin' and User.query.filter_by(role='admin').count() <= 1:
                flash("The last administrator account cannot be deleted.", "error")
            else:
                db.session.delete(u)
                db.session.commit()
                flash(f"User '{username}' deleted.", "success")
    return redirect(url_for('admin_panel'))

# --- INVENTORY & ASSET ROUTES ---
@app.route('/expense_payment_receipt/<int:payment_id>')
def view_payment_receipt(payment_id):
    if 'user' not in session: return redirect(url_for('login'))
    payment = db.session.get(PaymentHistory, payment_id)
    if not payment or not payment.receipt_file: return "Receipt not found.", 404
    # FIX 9: Served securely from instance folder instead of public static folder
    return send_from_directory(app.config['UPLOAD_FOLDER'], payment.receipt_file, as_attachment=False)

@app.route('/store/approve-issue/<int:req_id>', methods=['POST'])
def approve_and_issue_requisition(req_id):
    if 'user' not in session or session.get('role') != 'admin': return redirect(url_for('login'))
    req = db.session.get(StoreRequisition, req_id)
    if not req: return "Requisition not found.", 404
    if req.status != 'PENDING':
        flash("Requisition has already been processed.", "error")
        return redirect(url_for('inventory_dashboard'))
    if req.submitted_by == session.get('user'):
        flash("A different administrator must approve this requisition.", "error")
        return redirect(url_for('inventory_dashboard'))
    item = req.item
    if not item.is_consumable:
        flash("Only consumable items can be issued through requisitions.", "error")
        return redirect(url_for('inventory_dashboard'))
    if req.quantity > item.current_stock:
        flash(f"Cannot issue. Insufficient stock! Available: {item.current_stock}", "error")
        return redirect(url_for('inventory_dashboard'))
        
    claimed = db.session.execute(
        update(StoreRequisition).where(StoreRequisition.id == req.id, StoreRequisition.status == 'PENDING')
        .values(status='ISSUED', issued_by=session.get('user'))
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("Requisition has already been processed.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    updated = db.session.execute(
        update(InventoryItem).where(InventoryItem.id == item.id, InventoryItem.current_stock >= req.quantity)
        .values(current_stock=InventoryItem.current_stock - req.quantity)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Cannot issue: stock changed or is insufficient.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    db.session.expire(item, ['current_stock'])
    db.session.add(StockMovement(item_id=item.id, movement_type='OUT', quantity=req.quantity, department_id=req.department_id, reference=f"Requisition #{req.id}", created_by=session.get('user')))
    db.session.commit()
    flash(f"Approved and issued {req.quantity} {item.unit_of_measure} of {item.name} to {req.department.name}.", "success")
    return redirect(url_for('inventory_dashboard'))

@app.route('/assets/return/<int:track_id>', methods=['POST'])
def return_asset(track_id):
    if 'user' not in session or session.get('role') != 'admin': return redirect(url_for('login'))
    tracking = db.session.get(AssetTracking, track_id)
    if not tracking: return "Asset tracking record not found.", 404
    if tracking.status != 'OUT':
        flash("This asset tracking record is already closed.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.issued_by == session.get('user'):
        flash("A different administrator must verify the asset return.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    condition = request.form.get('condition', 'GOOD')
    notes = request.form.get('inspection_notes', '').strip()
    if condition not in {'GOOD', 'DAMAGED_LOST'} or len(notes) > 2000 or (condition == 'DAMAGED_LOST' and not notes):
        flash("Enter a valid return condition and inspection note.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    returned_at = datetime.utcnow()
    tracking_status = 'RETURNED_GOOD' if condition == 'GOOD' else 'DAMAGED_LOST'
    claimed = db.session.execute(
        update(AssetTracking).where(AssetTracking.id == tracking.id, AssetTracking.status == 'OUT',
        or_(AssetTracking.issued_by.is_(None), AssetTracking.issued_by != session.get('user')))
        .values(status=tracking_status, returned_at=returned_at, inspection_notes=notes or None,
        recovery_triggered=condition == 'DAMAGED_LOST', returned_by=session.get('user'))
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("This asset tracking record has already been closed.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    if condition == 'GOOD':
        db.session.execute(update(InventoryItem).where(InventoryItem.id == tracking.item_id).values(current_stock=InventoryItem.current_stock + tracking.quantity))
        db.session.add(StockMovement(item_id=tracking.item_id, movement_type='IN', quantity=tracking.quantity, department_id=tracking.department_id, reference=f"Asset return #{tracking.id}", created_by=session.get('user')))
        flash("Asset returned in good condition and restocked.", "success")
    else:
        flash(f"Damage/loss recorded. Recovery workflow flagged for {tracking.quantity} {tracking.item.name}.", "error")
    db.session.commit()
    return redirect(url_for('inventory_dashboard'))

@app.route('/assets/recovery/<int:track_id>', methods=['POST'])
def create_asset_recovery_bill(track_id):
    if 'user' not in session or session.get('role') != 'admin': return redirect(url_for('login'))
    tracking = db.session.get(AssetTracking, track_id)
    if not tracking: return "Asset tracking record not found.", 404
    if tracking.status != 'DAMAGED_LOST' or not tracking.recovery_triggered:
        flash("Only recorded damaged or lost assets can be sent for recovery.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.recovery_expense_id:
        flash("A recovery document already exists for this asset record.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.returned_by == session.get('user'):
        flash("A different administrator must prepare the recovery document.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.item.unit_cost is None or tracking.item.unit_cost <= 0:
        flash("Set a valid inventory unit cost before estimating recovery.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    recovery_amount = (Decimal(tracking.item.unit_cost) * tracking.quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if not recovery_amount.is_finite() or recovery_amount > Decimal('999999999.99'):
        flash("The asset recovery estimate is outside the supported amount range.", "error")
        return redirect(url_for('inventory_dashboard'))
        
    expense_id = generate_prefixed_id('RECONCILIATION')
    recovery = Expense(id=expense_id, doc_type='Recovery', date_created=datetime.now().strftime('%Y-%m-%d %H:%M'),
                       caretaker_name=tracking.borrower_name,
                       item_description=f"Book-value recovery estimate for {tracking.quantity} {tracking.item.unit_of_measure} {tracking.item.name} (asset record #{tracking.id})",
                       estimated_amount=f"{recovery_amount:.2f}", actual_amount='0.00', status='Pending Approval',
                       logged_by=session.get('user'), audit_ref=f"AST-{tracking.id}", linked_asset_tracking_id=tracking.id)
    claimed = db.session.execute(
        update(AssetTracking).where(AssetTracking.id == tracking.id, AssetTracking.recovery_expense_id.is_(None), AssetTracking.status == 'DAMAGED_LOST')
        .values(recovery_expense_id=expense_id)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("A recovery document was already created for this record.", "error")
        return redirect(url_for('inventory_dashboard'))
    db.session.add(recovery)
    db.session.commit()
    flash(f"Recovery document {expense_id} created using book value as an estimate; confirm the amount against replacement/repair evidence before payment.", "success")
    return redirect(url_for('expenses_panel'))

# --- EXPENSES & TRANSFERS ---
@app.route('/return_transfer/<transfer_id>', methods=['POST'])
def return_transfer(transfer_id):
    if 'user' not in session or session.get('role') != 'admin': return redirect(url_for('login'))
    transfer = AssetTransfer.query.get(transfer_id)
    if not transfer: return "Transfer not found", 404
    if transfer.status in {'Fully Returned', 'Resolved & Paid'} or (transfer.status == 'Discrepancy' and transfer.recovery_bill_id):
        flash("This transfer has already been reconciled.", "error")
        return redirect(url_for('transfers_panel'))
    if transfer.approved_by == session.get('user'):
        flash("A different administrator must reconcile this transfer.", "error")
        return redirect(url_for('transfers_panel'))
    if transfer.reconciled_by == session.get('user'):
        flash("A different administrator must verify this reconciliation.", "error")
        return redirect(url_for('transfers_panel'))
        
    items = TransferItem.query.filter_by(transfer_id=transfer.id).all()
    current_status = transfer.status
    return_values = []
    try:
        for index, item in enumerate(items):
            if f'good_{index}' not in request.form or f'damaged_{index}' not in request.form: raise ValueError
            good = int(request.form.get(f'good_{index}', '0'))
            damaged = int(request.form.get(f'damaged_{index}', '0'))
            if good < 0 or damaged < 0 or good + damaged > item.sent: raise ValueError
            return_values.append((item, good, damaged, item.sent - good - damaged))
    except ValueError:
        flash("Returned and damaged quantities must be valid and cannot exceed sent quantities.", "error")
        return redirect(url_for('transfers_panel'))
        
    has_discrepancy = any(damaged or missing for _, _, damaged, missing in return_values)
    discrepancy_notes = request.form.get('discrepancy_notes', '').strip()
    if has_discrepancy and not discrepancy_notes:
        flash("Explain the shortage or damage before reconciling this transfer.", "error")
        return redirect(url_for('transfers_panel'))
    if len(discrepancy_notes) > 2000:
        flash("Discrepancy notes must be 2000 characters or fewer.", "error")
        return redirect(url_for('transfers_panel'))
        
    new_status = 'Discrepancy' if has_discrepancy else 'Fully Returned'
    update_conditions = [
        AssetTransfer.id == transfer.id, AssetTransfer.status == current_status,
        or_(AssetTransfer.approved_by.is_(None), AssetTransfer.approved_by != session.get('user')),
        or_(AssetTransfer.reconciled_by.is_(None), AssetTransfer.reconciled_by != session.get('user'))
    ]
    claimed = db.session.execute(
        update(AssetTransfer).where(*update_conditions).values(status=new_status, discrepancy_notes=discrepancy_notes or 'N/A', reconciled_by=session.get('user'))
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("This transfer was updated by another administrator.", "error")
        return redirect(url_for('transfers_panel'))
        
    for item, good, damaged, missing in return_values:
        item.returned_good = good
        item.damaged = damaged
        item.missing = missing
        if item.item_id and good:
            db.session.execute(update(InventoryItem).where(InventoryItem.id == item.item_id).values(current_stock=InventoryItem.current_stock + good))
            db.session.add(StockMovement(item_id=item.item_id, movement_type='IN', quantity=good, reference=f"Transfer return {transfer.id}", created_by=session.get('user')))
    db.session.commit()
    flash(f"Return details saved for transfer {transfer.id}.", "success")
    return redirect(url_for('transfers_panel'))

# --- HR & ATTENDANCE ROUTES ---
# FIX 10: Resolved N+1 Query problem in HR reports to drastically improve performance
def get_staff_attendance_report(start_date='', end_date='', department='ALL'):
    staff_query = Staff.query
    if department != 'ALL': staff_query = staff_query.filter_by(department=department)
    staff_list = staff_query.order_by(Staff.name).all()
    if not staff_list: return []
    
    staff_ids = [s.id for s in staff_list]
    attendance_query = Attendance.query.filter(Attendance.staff_id.in_(staff_ids))
    if start_date and end_date:
        attendance_query = attendance_query.filter(Attendance.date >= start_date, Attendance.date <= end_date)
    
    all_logs = attendance_query.all()
    logs_by_staff = {}
    for log in all_logs:
        logs_by_staff.setdefault(log.staff_id, []).append(log)
        
    report_data = []
    for staff in staff_list:
        logs = logs_by_staff.get(staff.id, [])
        report_data.append({
            'staff': {'id': staff.id, 'name': staff.name, 'department': staff.department},
            'days_worked': sum(1 for log in logs if log.status != 'ABSENT'),
            'off_day_work': sum(1 for log in logs if log.is_off_day_work),
            'holiday_work': sum(1 for log in logs if log.is_holiday_work),
            'total_hours': sum(log.hours_worked for log in logs if log.status != 'ABSENT'),
            'overtime_hours': sum(max(0.0, log.hours_worked - 8) for log in logs if log.status != 'ABSENT'),
            'undertime_hours': sum(max(0.0, 8 - log.hours_worked) for log in logs if log.status == 'PRESENT' and log.hours_worked < 8)
        })
    return report_data

@app.route('/hr/report', methods=['GET'])
def hr_payroll_report():
    if 'user' not in session: return redirect(url_for('login'))
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    dept_filter = request.args.get('department', 'ALL')
    if start_date or end_date:
        if not is_valid_iso_date(start_date) or not is_valid_iso_date(end_date): return "Start and end dates must use YYYY-MM-DD format.", 400
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        if end < start: return "End date cannot be earlier than start date.", 400
        
    staff_query = Staff.query
    if dept_filter != 'ALL': staff_query = staff_query.filter_by(department=dept_filter)
    staff_list = staff_query.all()
    
    # FIX 11: Resolved N+1 Query problem here as well
    staff_ids = [s.id for s in staff_list]
    logs_by_staff = {}
    if staff_ids:
        logs_query = Attendance.query.filter(Attendance.staff_id.in_(staff_ids))
        if start_date and end_date:
            logs_query = logs_query.filter(Attendance.date >= start_date, Attendance.date <= end_date)
        for log in logs_query.all():
            logs_by_staff.setdefault(log.staff_id, []).append(log)
            
    report_data = []
    for staff in staff_list:
        logs = logs_by_staff.get(staff.id, [])
        report_data.append({
            'staff': {'id': staff.id, 'name': staff.name, 'department': staff.department},
            'days_worked': sum(1 for l in logs if l.status != 'ABSENT'),
            'off_day_work': sum(1 for l in logs if l.is_off_day_work),
            'holiday_work': sum(1 for l in logs if l.is_holiday_work),
            'total_hours': sum(l.hours_worked for l in logs if l.status != 'ABSENT'),
            'overtime_hours': sum(max(0.0, l.hours_worked - 8) for l in logs if l.status != 'ABSENT'),
            'undertime_hours': sum(max(0.0, 8 - l.hours_worked) for l in logs if l.status == 'PRESENT' and l.hours_worked < 8)
        })
        
    departments = [d[0] for d in db.session.query(Staff.department).distinct().all()]
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')): return render_template('react_app.html')
    return render_template('hr_report.html', report_data=report_data, start_date=start_date, end_date=end_date, dept_filter=dept_filter, departments=departments, user=session.get('user'), role=session.get('role'))

def validated_report_dates(start_date, end_date):
    if not start_date and not end_date: return True
    if not is_valid_iso_date(start_date) or not is_valid_iso_date(end_date): return False
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    return end >= start

def attendance_rows(report_data):
    for row in report_data:
        staff = row['staff']
        yield (staff['id'], staff['name'], staff['department'], row['days_worked'], row['off_day_work'], row['holiday_work'], f"{row['total_hours']:.2f}", f"{row['overtime_hours']:.2f}", f"{row['undertime_hours']:.2f}")

def csv_download(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([spreadsheet_safe_cell(value) for value in row] for row in rows)
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})

def excel_download(filename, headers, rows):
    # FIX 12: Cleaned up malformed HTML tags with spaces
    head = ''.join(f'<th>{escape(str(value))}</th>' for value in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{escape(spreadsheet_safe_cell(value))}</td>' for value in row) + '</tr>' for row in rows)
    content = f'<html><head><meta charset="utf-8"></head><body><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>'
    return Response(content, mimetype='application/vnd.ms-excel', headers={'Content-Disposition': f'attachment; filename={filename}'})

# --- CSV & EXPORT ROUTES ---
@app.route('/export_csv')
def export_csv():
    if 'user' not in session: return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Date', 'Customer Name', 'Category', 'Pax', 'Amount', 'Status', 'Payment Method', 'Reference'])
    for t in Transaction.query.all():
        writer.writerow([spreadsheet_safe_cell(value) for value in (t.id, t.date, t.customer_name, t.category, t.pax, t.amount, t.status, t.payment_method, t.reference)])
    output.seek(0)
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=thika_gymkhana_transactions.csv"})

# FIX 13: Corrected main execution dunder
if __name__ == '__main__':
    app.run(debug=True, port=5000)