from flask import (
    Flask, render_template, request, redirect, url_for, Response, flash,
    get_flashed_messages,
    session, send_from_directory
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

app = Flask(__name__)
configured_secret_key = os.environ.get('FLASK_SECRET_KEY')
if os.environ.get('APP_ENV', 'development').lower() == 'production' and not configured_secret_key:
    raise RuntimeError("FLASK_SECRET_KEY must be configured in production.")
app.secret_key = configured_secret_key or secrets.token_hex(32)

# Configure SQLite Database
os.makedirs('instance', exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:///club.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
db = SQLAlchemy(app)

UPLOAD_FOLDER = 'static/uploads'
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
    is_consumable = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )

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
    'Housekeeping': (
        'Cleaning Supplies', 'Paper & Tissue', 'Laundry & Linen',
        'Waste & Hygiene', 'Other'
    ),
    'Food & Beverage': (
        'Bar Beverages', 'Kitchen Supplies', 'Food Ingredients',
        'Service Disposables', 'Other'
    ),
    'Maintenance & Engineering': (
        'Electrical', 'Plumbing', 'Building & Carpentry', 'Paint & Hardware',
        'Tools', 'Other'
    ),
    'Events & Banqueting': (
        'Tableware', 'Event Furniture', 'Decor', 'Audio-Visual Equipment',
        'Other'
    ),
    'Sports & Recreation': (
        'Sports Equipment', 'Pool Supplies', 'Grounds & Landscaping', 'Other'
    ),
    'Office & Administration': (
        'Stationery', 'Printing', 'IT Accessories', 'Other'
    ),
    'Safety & Security': (
        'Personal Protective Equipment', 'First Aid', 'Fire Safety',
        'Security Equipment', 'Other'
    ),
    'Furniture & Appliances': (
        'Furniture', 'Kitchen Appliances', 'Other Equipment', 'Other'
    )
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
            'inventory_items': {
                'is_consumable': 'BOOLEAN NOT NULL DEFAULT TRUE',
                'subcategory': "VARCHAR(60) NOT NULL DEFAULT 'Other'"
            },
            'payment_history': {
                'invoice_ref': 'VARCHAR(50)',
                'receipt_file': 'VARCHAR(255)',
                'receipt_note': 'VARCHAR(500)',
                'variance_reason': 'VARCHAR(500)'
            },
            'store_requisitions': {
                'submitted_by': 'VARCHAR(64)',
                'issued_by': 'VARCHAR(64)',
                'rejected_by': 'VARCHAR(64)',
                'rejection_reason': 'VARCHAR(500)'
            },
            'asset_tracking': {
                'issued_by': 'VARCHAR(64)',
                'returned_by': 'VARCHAR(64)',
                'recovery_expense_id': 'VARCHAR(32)'
            },
            'asset_transfers': {
                'reconciled_by': 'VARCHAR(64)'
            },
            'transfer_items': {
                'item_id': 'INTEGER'
            },
            'expenses': {
                'linked_asset_tracking_id': 'INTEGER',
                'approved_by': 'VARCHAR(64)',
                'approved_at': 'VARCHAR(30)',
                'rejected_by': 'VARCHAR(64)',
                'rejection_reason': 'VARCHAR(500)'
            },
            'stock_movements': {
                'created_by': 'VARCHAR(64)'
            }
        }
        with db.engine.begin() as connection:
            for table, columns in migrations.items():
                existing_columns = {
                    column['name']
                    for column in inspect(connection).get_columns(table)
                }
                for column, definition in columns.items():
                    if column not in existing_columns:
                        connection.exec_driver_sql(
                            f'ALTER TABLE {table} ADD COLUMN {column} {definition}'
                        )
            duplicate_attendance = connection.exec_driver_sql(
                'SELECT 1 FROM attendance GROUP BY date, staff_id '
                'HAVING COUNT(*) > 1 LIMIT 1'
            ).first()
            if duplicate_attendance:
                app.logger.warning(
                    'Attendance uniqueness index was not created because '
                    'duplicate date/staff records already exist.'
                )
            else:
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_date_staff '
                    'ON attendance (date, staff_id)'
                )
        # Seed default services if empty
        if not Service.query.first():
            defaults = {
                'Temporary Guest Fee': 200.0,
                'Swimming Fee': 200.0,
                'Sports Fee': 150.0,
                'Hiring Fee': 300.0
            }
            for name, price in defaults.items():
                db.session.add(Service(name=name, price=price))
            db.session.commit()

        # Seed initial admin if specified and user table is empty
        if not User.query.first():
            initial_password = os.environ.get('INITIAL_ADMIN_PASSWORD')
            if initial_password:
                if len(initial_password) < 8:
                    raise RuntimeError(
                        "INITIAL_ADMIN_PASSWORD must be at least 8 characters."
                    )
                username = os.environ.get('INITIAL_ADMIN_USERNAME', 'admin').strip().lower()
                name = os.environ.get('INITIAL_ADMIN_NAME', 'Administrator').strip()
                if not username or not name:
                    raise RuntimeError(
                        "INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_NAME cannot be empty."
                    )
                admin_user = User(
                    username=username,
                    name=name,
                    password_hash=generate_password_hash(initial_password),
                    role='admin'
                )
                db.session.add(admin_user)
                db.session.commit()

init_db()

# --- HELPER FUNCTIONS ---
def generate_prefixed_id(doc_type, category_name=None, existing_records=None):
    doc_type_upper = doc_type.upper().strip()

    if doc_type_upper in {'RECONCILIATION', 'REC'}:
        prefix = 'REC'
    elif doc_type_upper in {'REQUISITION', 'BIL', 'BILL_OF_REQUISITION'}:
        prefix = 'BIL'
    elif doc_type_upper in {'TRANSFER', 'TRF'}:
        prefix = 'TRF'
    elif doc_type_upper == 'LPO':
        prefix = 'LPO'
    elif doc_type_upper == 'SERVICE_BILL' and category_name:
        words = category_name.strip().split()
        prefix = ''.join(word[0] for word in words).upper() if words else 'BIL'
    else:
        prefix = doc_type_upper[:3]

    while True:
        random_digits = ''.join(random.choices(string.digits, k=4))
        full_code = f"{prefix}{random_digits}"
        # Check uniqueness across database tables depending on doc type
        exists = False
        if prefix == 'LPO' or prefix == 'BIL' or prefix == 'REC':
            exists = Expense.query.get(full_code) is not None
        elif prefix == 'TRF':
            exists = AssetTransfer.query.get(full_code) is not None
        else:
            exists = Transaction.query.get(full_code) is not None

        if not exists:
            return full_code

def fmt_kes(val):
    try:
        amount = Decimal(str(val))
        return f"{amount:,.2f}" if amount.is_finite() else "0.00"
    except (InvalidOperation, ValueError, TypeError):
        return "0.00"

app.jinja_env.filters['fmt_kes'] = fmt_kes
app.jinja_env.filters['kes'] = fmt_kes

@app.template_filter('comma')
def format_comma(value):
    try:
        if value is None or value == '':
            return "0.00"
        amount = Decimal(str(value))
        return f"{amount:,.2f}" if amount.is_finite() else "0.00"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def spreadsheet_safe_cell(value):
    text = '' if value is None else str(value)
    if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) or text.lstrip().startswith(('=', '+', '-', '@')):
        return f"'{text}"
    return text

class TemplateRecord(dict):
    def __getattr__(self, name):
        return self.get(name)

def is_valid_iso_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''):
        return False
    try:
        datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return False
    return True

def get_services():
    return {s.name: s.price for s in Service.query.all()}

def get_users():
    return {
        u.username: {
            'name': u.name,
            'password_hash': u.password_hash,
            'role': u.role
        }
        for u in User.query.all()
    }

def get_expenses():
    return Expense.query.order_by(Expense.date_created.desc()).all()

def get_transfers():
    return AssetTransfer.query.order_by(AssetTransfer.date_sent.desc()).all()

def get_transfer_items(transfer):
    items = TransferItem.query.filter_by(transfer_id=transfer.id).all()
    if items:
        return [{'name': i.name, 'sent': i.sent, 'returned_good': i.returned_good, 'damaged': i.damaged, 'missing': i.missing} for i in items]
    return []

# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
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
                return {
                    'authenticated': True,
                    'user': username,
                    'display_name': session['display_name'],
                    'role': user_rec.role
                }
            flash(f"Welcome back, {session['display_name']}!", "success")
            return redirect(url_for('index'))
        else:
            if request.is_json:
                return {'error': 'Invalid username or password.'}, 401
            flash("❌ Invalid username or password.", "error")
            
    if User.query.count() == 0:
        return render_template('login.html', bootstrap_required=True)
    return render_template('react_app.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out securely.", "success")
    return redirect(url_for('login'))

# --- MAIN POS & BILLING ROUTES ---
@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')

    today_str = datetime.now().strftime('%Y-%m-%d')
    selected_date = request.args.get('date', today_str)
    search_query = request.args.get('search', '').strip().lower()
    selected_cashier = request.args.get('cashier_filter', 'all').lower()
    selected_status = request.args.get('status_filter', 'all').lower()
    
    query = Transaction.query
    if selected_date:
        query = query.filter(Transaction.date.startswith(selected_date))
    
    transactions = query.all()
    filtered_transactions = []
    
    for t in transactions:
        matches_search = (
            not search_query or 
            search_query in t.customer_name.lower() or 
            search_query in t.id.lower() or
            search_query in t.reference.lower()
        )
        
        creator = (t.created_by or '').lower()
        confirmer = (t.confirmed_by or '').lower()
        matches_cashier = (selected_cashier == 'all') or (selected_cashier in [creator, confirmer])
        
        status_val = t.status.lower()
        matches_status = (selected_status == 'all') or (selected_status == status_val)
        
        if matches_search and matches_cashier and matches_status:
            # Convert to dictionary or format for template compatibility
            filtered_transactions.append({
                'id': t.id,
                'date': t.date,
                'customer_name': t.customer_name,
                'category': t.category,
                'pax': t.pax,
                'amount': t.amount,
                'status': t.status,
                'payment_method': display_payment_method(t.payment_method),
                'reference': t.reference,
                'created_by': t.created_by,
                'confirmed_by': t.confirmed_by
            })
    
    date_filtered_txs = Transaction.query.filter(Transaction.date.startswith(selected_date)).all()
    paid_txs = [t for t in date_filtered_txs if t.status == 'Paid']
    
    mpesa_revenue = sum(
        (Decimal(t.amount) for t in paid_txs if t.payment_method in MPESA_PAYMENT_METHODS),
        Decimal('0.00')
    )
    cash_revenue = sum(
        (Decimal(t.amount) for t in paid_txs if t.payment_method == 'Cash'),
        Decimal('0.00')
    )
    other_revenue = sum(
        (
            Decimal(t.amount) for t in paid_txs
            if t.payment_method not in MPESA_PAYMENT_METHODS | {'Cash'}
        ),
        Decimal('0.00')
    )
    total_revenue = mpesa_revenue + cash_revenue + other_revenue
    
    services = get_services()
    categories = {cat: Decimal('0.00') for cat in services.keys()}
    for t in paid_txs:
        if t.category in categories:
            categories[t.category] += Decimal(t.amount)
            
    users = get_users()
    
    return render_template(
        'index.html', 
        transactions=filtered_transactions, 
        total_revenue=total_revenue, 
        mpesa_revenue=mpesa_revenue,
        cash_revenue=cash_revenue,
        other_revenue=other_revenue,
        categories=categories,
        fee_rates=services,
        users=users,
        selected_date=selected_date,
        search_query=search_query,
        selected_cashier=selected_cashier,
        selected_status=selected_status,
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/shift_report')
def shift_report():
    if 'user' not in session:
        return redirect(url_for('login'))
    report_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    if not is_valid_iso_date(report_date):
        return "Date must use YYYY-MM-DD format.", 400
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')

    transactions = Transaction.query.filter(
        Transaction.date.startswith(report_date),
        Transaction.status == 'Paid'
    ).order_by(Transaction.date, Transaction.id).all()
    mpesa_revenue = sum(
        (
            Decimal(tx.amount) for tx in transactions
            if tx.payment_method in MPESA_PAYMENT_METHODS
        ),
        Decimal('0.00')
    )
    cash_revenue = sum(
        (Decimal(tx.amount) for tx in transactions if tx.payment_method == 'Cash'),
        Decimal('0.00')
    )
    other_revenue = sum(
        (
            Decimal(tx.amount) for tx in transactions
            if tx.payment_method not in MPESA_PAYMENT_METHODS | {'Cash'}
        ),
        Decimal('0.00')
    )
    return render_template(
        'shift_report.html',
        date=report_date,
        transactions=transactions,
        mpesa_rev=mpesa_revenue,
        cash_rev=cash_revenue,
        other_rev=other_revenue,
        total_rev=mpesa_revenue + cash_revenue + other_revenue
    )

@app.route('/analytics')
def analytics():
    if 'user' not in session:
        return redirect(url_for('login'))
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    if not is_valid_iso_date(selected_date):
        return "Date must use YYYY-MM-DD format.", 400
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')

    service_query = request.args.get('service_search', '').strip()
    all_transactions = Transaction.query.filter(
        Transaction.date.startswith(selected_date)
    ).all()
    categories = set(get_services())
    categories.update(tx.category for tx in all_transactions)
    all_categories = sorted(categories)
    filtered_categories = [
        category for category in all_categories
        if not service_query or category == service_query
    ]
    category_bills = {
        category: [tx for tx in all_transactions if tx.category == category]
        for category in filtered_categories
    }
    category_revenue = {
        category: sum(
            (
                Decimal(tx.amount) for tx in all_transactions
                if tx.category == category and tx.status == 'Paid'
            ),
            Decimal('0.00')
        )
        for category in all_categories
    }
    category_counts = {
        category: sum(tx.pax for tx in category_bills[category])
        for category in filtered_categories
    }
    category_bills_count = {
        category: category_bills[category]
        for category in filtered_categories
    }
    total_revenue = sum(
        (category_revenue[category] for category in filtered_categories),
        Decimal('0.00')
    )
    total_transactions = sum(
        1 for tx in all_transactions
        if tx.status == 'Paid'
        and (not service_query or tx.category == service_query)
    )
    report_transactions = [
        tx for tx in all_transactions
        if not service_query or tx.category == service_query
    ]
    paid_transactions = [tx for tx in report_transactions if tx.status == 'Paid']
    pending_transactions = [
        tx for tx in report_transactions if tx.status == 'Pending'
    ]
    mpesa_revenue = sum(
        (
            Decimal(tx.amount) for tx in paid_transactions
            if tx.payment_method in MPESA_PAYMENT_METHODS
        ),
        Decimal('0.00')
    )
    cash_revenue = sum(
        (
            Decimal(tx.amount) for tx in paid_transactions
            if tx.payment_method == 'Cash'
        ),
        Decimal('0.00')
    )
    pending_bill_total = sum(
        (Decimal(tx.amount) for tx in pending_transactions), Decimal('0.00')
    )
    category_percentages = {
        category: round(
            category_revenue[category] / total_revenue * 100, 1
        ) if total_revenue else 0
        for category in filtered_categories
    }
    return render_template(
        'analytics.html',
        selected_date=selected_date,
        service_query=service_query,
        categories=category_revenue,
        filtered_categories=filtered_categories,
        category_counts=category_counts,
        category_bills=category_bills_count,
        category_percentages=category_percentages,
        total_revenue=total_revenue,
        total_transactions=total_transactions,
        mpesa_revenue=mpesa_revenue,
        cash_revenue=cash_revenue,
        pending_bill_count=len(pending_transactions),
        pending_bill_total=pending_bill_total,
        total_bill_count=len(report_transactions)
    )

@app.route('/create_bill', methods=['POST'])
def create_bill():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    services = get_services()
    category = request.form.get('category', '').strip()
    customer_name = request.form.get('customer_name', '').strip()
    try:
        pax = int(request.form.get('pax', ''))
    except ValueError:
        flash("Enter a valid guest quantity.", "error")
        return redirect(url_for('index'))
    if pax < 1 or not customer_name or len(customer_name) > 120 or category not in services:
        flash("Enter valid billing details.", "error")
        return redirect(url_for('index'))

    custom_amount = request.form.get('custom_amount', '').strip()
    try:
        total_amount = (
            Decimal(custom_amount)
            if custom_amount
            else Decimal(pax) * Decimal(str(services[category]))
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        flash("❌ Bill amount must be a valid number.", "error")
        return redirect(url_for('index'))
    if (
        not total_amount.is_finite() or total_amount <= 0
        or total_amount > Decimal('999999999.99')
    ):
        flash("❌ Bill amount must be between 0.01 and 999,999,999.99.", "error")
        return redirect(url_for('index'))

    new_id = generate_prefixed_id('SERVICE_BILL', category)
    display_name = f"{customer_name} ({pax} Pax)" if pax > 1 else customer_name

    new_tx = Transaction(
        id=new_id,
        date=datetime.now().strftime('%Y-%m-%d %H:%M'),
        customer_name=display_name,
        category=category,
        pax=pax,
        amount=f"{total_amount:.2f}",
        status='Pending',
        payment_method='Unpaid',
        reference='Pending',
        created_by=session.get('user'),
        confirmed_by='N/A'
    )
    db.session.add(new_tx)
    db.session.commit()
    return redirect(url_for('view_invoice', tx_id=new_id))

@app.route('/invoice/<tx_id>')
def view_invoice(tx_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    tx = Transaction.query.get(tx_id)
    if not tx:
        return "Bill not found", 404
    return render_template(
        'invoice.html',
        tx=tx,
        paybill=PAYBILL_NUMBER,
        mpesa_account=tx.id
    )

@app.route('/confirm_payment/<tx_id>', methods=['POST'])
def confirm_payment(tx_id):
    if 'user' not in session:
        return redirect(url_for('login'))
        
    tx = Transaction.query.get(tx_id)
    if not tx:
        return "Bill not found", 404

    current_date_prefix = tx.date[:10]
    if tx.status != 'Pending':
        flash(f"❌ Bill {tx_id} is not pending.", "error")
        return redirect(url_for('index', date=current_date_prefix))

    try:
        bill_amount = Decimal(tx.amount)
    except InvalidOperation:
        bill_amount = Decimal('NaN')
    if not bill_amount.is_finite() or bill_amount <= 0:
        flash("❌ This bill has an invalid amount and cannot be paid.", "error")
        return redirect(url_for('index', date=current_date_prefix))

    payment_method = request.form.get('payment_method', 'M-Pesa')
    ref_code = request.form.get('reference', '').strip().upper()
    if payment_method not in {'Cash', 'M-Pesa'}:
        flash("❌ Choose Cash or M-Pesa.", "error")
        return redirect(url_for('index', date=current_date_prefix))

    if payment_method == 'Cash':
        existing_refs = {transaction.reference for transaction in Transaction.query.all()}
        while True:
            ref_code = f"CASH{secrets.randbelow(9000) + 1000}"
            if ref_code not in existing_refs:
                break
    else:
        if not ref_code or len(ref_code) != 10 or not ref_code.isalnum():
            flash("⚠️ Valid 10-character alphanumeric M-Pesa reference required.", "error")
            return redirect(url_for('index', date=current_date_prefix))
        existing_ref = Transaction.query.filter_by(
            status='Paid', reference=ref_code
        ).first()
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
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template('admin.html', users=get_users(), user=session.get('user'), role=session.get('role'))

@app.route('/admin/users/<username>/update', methods=['POST'])
def update_user(username):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    user = User.query.get(username)
    new_username = request.form.get('username', '').strip().lower()
    name = request.form.get('name', '').strip()
    role = request.form.get('role', '')
    if not user:
        return "User not found", 404
    if not new_username or len(new_username) > 64 or not name or len(name) > 120 or role not in {'admin', 'cashier'}:
        flash("Enter a valid username, name, and role.", "error")
        return redirect(url_for('admin_panel'))
    if new_username != username and User.query.get(new_username):
        flash(f"User '{new_username}' already exists.", "error")
        return redirect(url_for('admin_panel'))
    if user.role == 'admin' and role != 'admin' and User.query.filter_by(role='admin').count() <= 1:
        flash("The last administrator cannot be changed to a cashier.", "error")
        return redirect(url_for('admin_panel'))

    user.username = new_username
    user.name = name
    user.role = role
    db.session.commit()
    if session.get('user') == username:
        session['user'] = new_username
        session['role'] = role
        session['display_name'] = name
    flash(f"User '{new_username}' updated.", "success")
    return redirect(url_for('admin_panel'))

@app.route('/admin/users/<username>/password', methods=['POST'])
def change_user_password(username):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    user = User.query.get(username)
    if not user:
        return "User not found", 404
    password = request.form.get('password', '')
    if len(password) < 8:
        flash("Password must be at least 8 characters long.", "error")
        return redirect(url_for('admin_panel'))
    user.password_hash = generate_password_hash(password)
    db.session.commit()
    flash(f"Password changed for '{username}'.", "success")
    return redirect(url_for('admin_panel'))

@app.route('/manage_services', methods=['GET', 'POST'])
def manage_services():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            name = request.form.get('service_name', '').strip()
            raw_price = request.form.get('price', '').strip()
            try:
                price = Decimal(raw_price)
            except InvalidOperation:
                price = Decimal('NaN')
            if (
                name and len(name) <= 100 and price.is_finite() and price > 0
                and price <= Decimal('999999999.99')
                and not Service.query.get(name)
            ):
                db.session.add(Service(name=name, price=float(price)))
                db.session.commit()
                flash(f"Service '{name}' added.", "success")
            else:
                flash("Enter a unique service name and a valid positive price.", "error")
        elif action == 'update':
            old_name = request.form.get('old_name', '').strip()
            new_name = request.form.get('new_name', '').strip()
            raw_price = request.form.get('price', '').strip()
            service = db.session.get(Service, old_name)
            try:
                price = Decimal(raw_price)
            except InvalidOperation:
                price = Decimal('NaN')
            conflicting_service = (
                db.session.get(Service, new_name) if new_name != old_name else None
            )
            if (
                service and new_name and len(new_name) <= 100
                and not conflicting_service and price.is_finite()
                and price > 0 and price <= Decimal('999999999.99')
            ):
                service.name = new_name
                service.price = float(price)
                db.session.commit()
                flash(f"Service '{new_name}' updated.", "success")
            else:
                flash("Enter a valid service name and positive price.", "error")
        elif action == 'delete':
            name = request.form.get('service_name', '')
            srv = Service.query.get(name)
            if srv:
                db.session.delete(srv)
                db.session.commit()
                flash(f"✅ Service '{name}' removed.", "success")
        return redirect(url_for('manage_services'))

    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template('manage_services.html', services=get_services(), user=session.get('user'), role=session.get('role'))

@app.route('/admin/add_user', methods=['POST'])
def add_user():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    username = request.form.get('username', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'cashier')

    if User.query.get(username):
        flash(f"User '{username}' already exists.", "error")
    else:
        new_u = User(username=username, name=name, password_hash=generate_password_hash(password), role=role)
        db.session.add(new_u)
        db.session.commit()
        flash(f"User '{username}' created successfully!", "success")
    return redirect(url_for('admin_panel'))

@app.route('/admin/users/<username>/delete', methods=['POST'])
def delete_user(username):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
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

@app.route('/admin/clear-operational-data', methods=['POST'])
def clear_operational_data():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    administrator = db.session.get(User, session.get('user'))
    password = request.form.get('current_password', '')
    if (
        not administrator or administrator.role != 'admin'
        or not check_password_hash(administrator.password_hash, password)
    ):
        flash("Operational data was not cleared. The administrator password is incorrect.", "error")
        return redirect(url_for('admin_panel'))

    deletion_order = [
        ('attendance records', Attendance),
        ('staff records', Staff),
        ('payment records', PaymentHistory),
        ('procurement documents', Expense),
        ('sales transactions', Transaction),
        ('asset transfer items', TransferItem),
        ('asset transfers', AssetTransfer),
        ('store requisitions', StoreRequisition),
        ('asset tracking records', AssetTracking),
        ('stock movements', StockMovement),
        ('inventory items', InventoryItem),
    ]
    try:
        deleted_counts = []
        for label, model in deletion_order:
            deleted = db.session.query(model).delete(synchronize_session=False)
            if deleted:
                deleted_counts.append(f"{deleted} {label}")
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Failed to clear operational data")
        flash("Operational data could not be cleared. No changes were saved.", "error")
        return redirect(url_for('admin_panel'))

    summary = ", ".join(deleted_counts) or "no operational records were present"
    flash(f"Operational data cleared successfully: {summary}. User accounts, service pricing, and departments were retained.", "success")
    return redirect(url_for('admin_panel'))

# --- INVENTORY ROUTES ---
@app.route('/inventory', methods=['GET'])
def inventory_dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')

    items = InventoryItem.query.order_by(InventoryItem.name.asc()).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    recent_movements = StockMovement.query.order_by(
        StockMovement.created_at.desc()
    ).limit(15).all()
    requisitions = StoreRequisition.query.order_by(
        StoreRequisition.created_at.desc()
    ).all()
    active_assets = AssetTracking.query.filter_by(status='OUT').order_by(
        AssetTracking.borrowed_at.desc()
    ).all()
    recovery_cases = AssetTracking.query.filter(
        AssetTracking.recovery_triggered.is_(True)
    ).order_by(AssetTracking.returned_at.desc()).all()

    return render_template(
        'inventory.html',
        items=items,
        departments=departments,
        movements=recent_movements,
        requisitions=requisitions,
        active_assets=active_assets,
        recovery_cases=recovery_cases,
        is_admin=session.get('role') == 'admin',
        user=session.get('user')
    )

@app.route('/api/session', methods=['GET'])
def api_session():
    if 'user' not in session:
        return {'authenticated': False}, 401
    return {
        'authenticated': True,
        'user': session.get('user'),
        'display_name': session.get('display_name', session.get('user')),
        'role': session.get('role')
    }

@app.route('/api/bootstrap', methods=['GET'])
def api_bootstrap():
    if 'user' not in session:
        return {
            'authenticated': False,
            'bootstrap_required': User.query.count() == 0
        }, 401

    today = datetime.now().strftime('%Y-%m-%d')
    transactions = Transaction.query.filter(
        Transaction.date.startswith(today)
    ).all()
    paid_transactions = [transaction for transaction in transactions if transaction.status == 'Paid']
    return {
        'authenticated': True,
        'user': session.get('user'),
        'display_name': session.get('display_name', session.get('user')),
        'role': session.get('role'),
        'date': today,
        'inventory': {
            'item_count': InventoryItem.query.count(),
            'low_stock_count': InventoryItem.query.filter(
                InventoryItem.current_stock <= InventoryItem.reorder_level
            ).count(),
            'pending_requisitions': StoreRequisition.query.filter_by(
                status='PENDING'
            ).count(),
            'active_assets': db.session.query(
                db.func.coalesce(db.func.sum(AssetTracking.quantity), 0)
            ).filter_by(status='OUT').scalar(),
        },
        'staff_count': Staff.query.count(),
        'services': [{
            'name': service.name,
            'price': str(service.price)
        } for service in Service.query.order_by(Service.name.asc()).all()],
        'transactions': [{
            'id': transaction.id,
            'date': transaction.date,
            'customer_name': transaction.customer_name,
            'category': transaction.category,
            'pax': transaction.pax,
            'amount': transaction.amount,
            'status': transaction.status,
            'payment_method': display_payment_method(transaction.payment_method),
            'reference': transaction.reference,
            'created_by': transaction.created_by
        } for transaction in sorted(transactions, key=lambda row: row.date, reverse=True)],
        'revenue': str(sum(
            (Decimal(transaction.amount) for transaction in paid_transactions),
            Decimal('0.00')
        )),
        'messages': [
            {'category': category, 'message': message}
            for category, message in get_flashed_messages(with_categories=True)
        ]
    }

@app.route('/api/inventory', methods=['GET'])
def api_inventory():
    if 'user' not in session:
        return {'error': 'Authentication required.'}, 401

    items = InventoryItem.query.order_by(InventoryItem.name.asc()).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    movements = StockMovement.query.order_by(
        StockMovement.created_at.desc()
    ).limit(15).all()
    requisitions = StoreRequisition.query.order_by(
        StoreRequisition.created_at.desc()
    ).limit(100).all()
    active_assets = AssetTracking.query.filter_by(status='OUT').order_by(
        AssetTracking.borrowed_at.desc()
    ).all()
    recovery_cases = AssetTracking.query.filter(
        AssetTracking.recovery_triggered.is_(True)
    ).order_by(AssetTracking.returned_at.desc()).all()

    return {
        'categories': INVENTORY_CATEGORIES,
        'items': [{
            'id': item.id,
            'name': item.name,
            'category': item.category,
            'subcategory': item.subcategory,
            'unit_of_measure': item.unit_of_measure,
            'current_stock': item.current_stock,
            'reorder_level': item.reorder_level,
            'unit_cost': str(item.unit_cost) if item.unit_cost is not None else None,
            'is_consumable': item.is_consumable,
            'department': item.department.name if item.department else None
        } for item in items],
        'departments': [{
            'id': department.id,
            'name': department.name
        } for department in departments],
        'movements': [{
            'id': movement.id,
            'item': movement.item.name,
            'movement_type': movement.movement_type,
            'quantity': movement.quantity,
            'reference': movement.reference,
            'department': movement.department.name if movement.department else None,
            'created_at': movement.created_at.isoformat() if movement.created_at else None
        } for movement in movements],
        'requisitions': [{
            'id': requisition.id,
            'item': requisition.item.name,
            'department': requisition.department.name,
            'quantity': requisition.quantity,
            'unit_of_measure': requisition.item.unit_of_measure,
            'requester': requisition.requester_name,
            'status': requisition.status,
            'created_at': requisition.created_at.isoformat() if requisition.created_at else None,
            'rejection_reason': requisition.rejection_reason
        } for requisition in requisitions],
        'active_assets': [{
            'id': tracking.id,
            'item': tracking.item.name,
            'department': tracking.department.name,
            'borrower': tracking.borrower_name,
            'quantity': tracking.quantity,
            'unit_of_measure': tracking.item.unit_of_measure,
            'borrowed_at': tracking.borrowed_at.isoformat() if tracking.borrowed_at else None
        } for tracking in active_assets],
        'recovery_cases': [{
            'id': tracking.id,
            'item': tracking.item.name,
            'department': tracking.department.name,
            'borrower': tracking.borrower_name,
            'quantity': tracking.quantity,
            'status': tracking.status,
            'recovery_expense_id': tracking.recovery_expense_id
        } for tracking in recovery_cases]
    }

@app.route('/api/portal/<section>', methods=['GET'])
def api_portal(section):
    if 'user' not in session:
        return {'error': 'Authentication required.'}, 401

    if section == 'admin':
        if session.get('role') != 'admin':
            return {'error': 'Administrator access required.'}, 403
        return {
            'users': [{
                'username': user.username,
                'name': user.name,
                'role': user.role
            } for user in User.query.order_by(User.username).all()],
            'services': [{
                'name': service.name,
                'price': str(service.price)
            } for service in Service.query.order_by(Service.name).all()]
        }

    if section in {'expenses', 'procurement-analytics'}:
        expenses = get_expenses()
        payments = PaymentHistory.query.order_by(PaymentHistory.id.asc()).all()
        payments_by_expense = {}
        for payment in payments:
            payments_by_expense.setdefault(payment.expense_id, []).append({
                'id': payment.id,
                'amount': payment.amount,
                'date': payment.date,
                'paid_by': payment.paid_by,
                'invoice_ref': payment.invoice_ref,
                'receipt_file': payment.receipt_file,
                'receipt_note': payment.receipt_note,
                'variance_reason': payment.variance_reason
            })
        if section == 'expenses':
            selected_type = request.args.get('type', 'ALL').upper()
            filtered = [
                expense for expense in expenses
                if selected_type == 'ALL' or expense.doc_type.upper() == selected_type
            ]
            return {
                'expenses': [{
                    'id': expense.id,
                    'doc_type': expense.doc_type,
                    'date_created': expense.date_created,
                    'caretaker_name': expense.caretaker_name,
                    'item_description': expense.item_description,
                    'estimated_amount': expense.estimated_amount,
                    'actual_amount': expense.actual_amount,
                    'invoice_ref': expense.invoice_ref,
                    'receipt_file': expense.receipt_file,
                    'receipt_note': expense.receipt_note,
                    'status': expense.status,
                    'logged_by': expense.logged_by,
                    'approved_by': expense.approved_by,
                    'rejected_by': expense.rejected_by,
                    'rejection_reason': expense.rejection_reason,
                    'payments': payments_by_expense.get(expense.id, [])
                } for expense in filtered],
                'counts': {
                    'all': len(expenses),
                    'lpo': sum(expense.doc_type == 'LPO' for expense in expenses),
                    'bill': sum(expense.doc_type == 'Bill' for expense in expenses),
                    'recovery': sum(expense.doc_type == 'Recovery' for expense in expenses)
                },
                'spend_month': str(sum(
                    (Decimal(payment.amount) for payment in payments
                     if payment.date.startswith(datetime.now().strftime('%Y-%m'))),
                    Decimal('0.00')
                )),
                'spend_year': str(sum(
                    (Decimal(payment.amount) for payment in payments
                     if payment.date.startswith(datetime.now().strftime('%Y'))),
                    Decimal('0.00')
                ))
            }

        status_counts = {
            'Fulfilled': 0, 'Partially Paid': 0, 'Pending': 0,
            'Pending Approval': 0, 'Rejected': 0
        }
        records = []
        cost_centers = {}
        totals = {'committed': Decimal('0.00'), 'savings': Decimal('0.00'),
                  'overpay': Decimal('0.00'), 'partial': Decimal('0.00')}
        for expense in expenses:
            estimated = Decimal(expense.estimated_amount)
            actual = Decimal(expense.actual_amount)
            if expense.doc_type == 'LPO' and expense.status not in {'Pending Approval', 'Rejected'}:
                totals['committed'] += estimated
            if expense.status == 'Fulfilled':
                status_counts['Fulfilled'] += 1
                variance = estimated - actual
                if variance > 0:
                    totals['savings'] += variance
                    variance_type = 'Favorable Savings'
                elif variance < 0:
                    totals['overpay'] += abs(variance)
                    variance_type = 'Overpayment'
                else:
                    variance_type = 'Exact Match'
            elif expense.status == 'Partially Paid':
                status_counts['Partially Paid'] += 1
                totals['partial'] += max(estimated - actual, Decimal('0.00'))
                variance_type = 'Partial Balance Due'
            elif expense.status in {'Pending Approval', 'Rejected'}:
                status_counts[expense.status] += 1
                variance_type = None
            else:
                status_counts['Pending'] += 1
                variance_type = None
            cost_centers[expense.caretaker_name] = (
                cost_centers.get(expense.caretaker_name, Decimal('0.00')) + actual
            )
            if variance_type:
                records.append({
                    'id': expense.id,
                    'date': expense.date_created,
                    'item': expense.item_description,
                    'audit_ref': expense.audit_ref,
                    'invoice_ref': expense.invoice_ref,
                    'variance_type': variance_type,
                    'variance_amount': str(
                        abs(estimated - actual) if expense.status == 'Fulfilled'
                        else max(estimated - actual, Decimal('0.00'))
                    )
                })
        return {
            **{name: str(amount) for name, amount in totals.items()},
            'status_counts': status_counts,
            'cost_centers': [
                {'name': name, 'amount': str(amount)}
                for name, amount in sorted(
                    cost_centers.items(), key=lambda pair: pair[1], reverse=True
                )
            ],
            'records': records
        }

    if section == 'transfers':
        transfers = []
        today = datetime.now().date()
        for transfer in get_transfers():
            try:
                overdue = (
                    transfer.status == 'Active' and bool(transfer.return_date)
                    and datetime.strptime(transfer.return_date, '%Y-%m-%d').date() < today
                )
            except ValueError:
                overdue = False
            transfers.append({
                'id': transfer.id,
                'date_sent': transfer.date_sent,
                'origin': transfer.origin,
                'destination': transfer.destination,
                'requested_by': transfer.requested_by,
                'approved_by': transfer.approved_by,
                'reconciled_by': transfer.reconciled_by,
                'custodian': transfer.custodian,
                'return_date': transfer.return_date,
                'status': transfer.status,
                'discrepancy_notes': transfer.discrepancy_notes,
                'recovery_bill_id': transfer.recovery_bill_id,
                'is_overdue': overdue,
                'items': [{
                    'item_id': item.item_id,
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing
                } for item in TransferItem.query.filter_by(transfer_id=transfer.id).all()]
            })
        return {
            'transfers': transfers,
            'inventory_items': [{
                'id': item.id,
                'name': item.name,
                'current_stock': item.current_stock,
                'unit_of_measure': item.unit_of_measure
            } for item in InventoryItem.query.filter_by(
                is_consumable=False
            ).order_by(InventoryItem.name).all()],
            'active_count': sum(transfer['status'] == 'Active' for transfer in transfers),
            'overdue_count': sum(transfer['is_overdue'] for transfer in transfers),
            'discrepancy_count': sum(transfer['status'] == 'Discrepancy' for transfer in transfers)
        }

    if section == 'attendance':
        selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        selected_end_date = request.args.get('end_date', selected_date)
        max_date = datetime.now().strftime('%Y-%m-%d')
        if not is_valid_iso_date(selected_date) or not is_valid_iso_date(selected_end_date):
            return {'error': 'Dates must use YYYY-MM-DD format.'}, 400
        start_day = datetime.strptime(selected_date, '%Y-%m-%d').date()
        end_day = datetime.strptime(selected_end_date, '%Y-%m-%d').date()
        if (
            end_day < start_day
            or selected_end_date > max_date
            or (end_day - start_day).days >= 31
        ):
            return {'error': 'Attendance date range is invalid.'}, 400
        entries = Attendance.query.filter_by(date=selected_date).all()
        attendance_marked = Attendance.query.filter(
            Attendance.date >= selected_date,
            Attendance.date <= selected_end_date
        ).first() is not None
        return {
            'date': selected_date,
            'end_date': selected_end_date,
            'max_date': max_date,
            'attendance_marked': attendance_marked,
            'staff': [{
                'id': staff.id,
                'name': staff.name,
                'department': staff.department,
                'attendance': next(({
                    'status': entry.status,
                    'hours_worked': entry.hours_worked,
                    'is_off_day_work': entry.is_off_day_work,
                    'is_holiday_work': entry.is_holiday_work,
                    'notes': entry.notes or ''
                } for entry in entries if entry.staff_id == staff.id), None)
            } for staff in Staff.query.order_by(Staff.name).all()]
        }

    if section == 'hr-report':
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        department = request.args.get('department', 'ALL')
        if not validated_report_dates(start_date, end_date):
            return {'error': 'Invalid date range.'}, 400
        return {
            'start_date': start_date,
            'end_date': end_date,
            'department': department,
            'departments': sorted(set(
                row[0] for row in db.session.query(Staff.department).distinct().all()
            )),
            'rows': get_staff_attendance_report(start_date, end_date, department)
        }

    if section == 'staff':
        staff_id = request.args.get('id', '')
        staff = db.session.get(Staff, staff_id) if staff_id else None
        if staff:
            start_date = request.args.get('start_date', '')
            end_date = request.args.get('end_date', '')
            if not validated_report_dates(start_date, end_date):
                return {'error': 'Invalid date range.'}, 400
            query = Attendance.query.filter_by(staff_id=staff.id)
            if start_date and end_date:
                query = query.filter(
                    Attendance.date >= start_date,
                    Attendance.date <= end_date
                )
            history = query.order_by(Attendance.date.desc()).all()
            worked = [entry for entry in history if entry.status != 'ABSENT']
            return {
                'staff': {'id': staff.id, 'name': staff.name, 'department': staff.department},
                'start_date': start_date,
                'end_date': end_date,
                'summary': {
                    'total_days': len(worked),
                    'total_hours': sum(entry.hours_worked for entry in worked),
                    'off_day_shifts': sum(entry.is_off_day_work for entry in worked),
                    'holiday_shifts': sum(entry.is_holiday_work for entry in worked)
                },
                'history': [{
                    'date': entry.date, 'status': entry.status,
                    'hours_worked': entry.hours_worked,
                    'is_off_day_work': entry.is_off_day_work,
                    'is_holiday_work': entry.is_holiday_work,
                    'notes': entry.notes or ''
                } for entry in history]
            }
        return {'staff_count': Staff.query.count()}

    if section in {'sales-analytics', 'shift-report'}:
        selected_date = request.args.get(
            'date', datetime.now().strftime('%Y-%m-%d')
        )
        if not is_valid_iso_date(selected_date):
            return {'error': 'Date must use YYYY-MM-DD format.'}, 400
        transactions = Transaction.query.filter(
            Transaction.date.startswith(selected_date)
        ).order_by(Transaction.date, Transaction.id).all()
        if section == 'shift-report':
            transactions = [tx for tx in transactions if tx.status == 'Paid']
        services = get_services()
        categories = sorted(set(services) | {tx.category for tx in transactions})
        service_filter = request.args.get('service_search', '').strip()
        if service_filter:
            categories = [category for category in categories if category == service_filter]
        report_transactions = [
            tx for tx in transactions if tx.category in categories
        ]
        summary = {}
        for category in categories:
            matching = [tx for tx in report_transactions if tx.category == category]
            paid = [tx for tx in matching if tx.status == 'Paid']
            pending = [tx for tx in matching if tx.status == 'Pending']
            summary[category] = {
                'count': len(matching),
                'quantity': sum(tx.pax for tx in matching),
                'revenue': str(sum(
                    (Decimal(tx.amount) for tx in paid), Decimal('0.00')
                )),
                'paid_count': len(paid),
                'pending_count': len(pending),
                'pending_total': str(sum(
                    (Decimal(tx.amount) for tx in pending), Decimal('0.00')
                )),
                'mpesa_revenue': str(sum(
                    (
                        Decimal(tx.amount) for tx in paid
                        if tx.payment_method in MPESA_PAYMENT_METHODS
                    ),
                    Decimal('0.00')
                )),
                'cash_revenue': str(sum(
                    (
                        Decimal(tx.amount) for tx in paid
                        if tx.payment_method == 'Cash'
                    ),
                    Decimal('0.00')
                )),
                'transactions': [{
                    'id': tx.id,
                    'date': tx.date,
                    'customer_name': tx.customer_name,
                    'amount': tx.amount,
                    'status': tx.status,
                    'payment_method': display_payment_method(tx.payment_method),
                    'reference': tx.reference,
                    'pax': tx.pax
                } for tx in matching]
            }
        return {
            'date': selected_date,
            'categories': summary,
            'total_revenue': str(sum(
                (Decimal(tx.amount) for tx in report_transactions if tx.status == 'Paid'),
                Decimal('0.00')
            )),
            'mpesa_revenue': str(sum(
                (
                    Decimal(tx.amount) for tx in report_transactions
                    if tx.status == 'Paid'
                    and tx.payment_method in MPESA_PAYMENT_METHODS
                ),
                Decimal('0.00')
            )),
            'cash_revenue': str(sum(
                (
                    Decimal(tx.amount) for tx in report_transactions
                    if tx.status == 'Paid' and tx.payment_method == 'Cash'
                ),
                Decimal('0.00')
            )),
            'total_transactions': sum(
                tx.status == 'Paid' for tx in report_transactions
            ),
            'paid_bill_count': sum(
                tx.status == 'Paid' for tx in report_transactions
            ),
            'pending_bill_count': sum(
                tx.status == 'Pending' for tx in report_transactions
            ),
            'pending_bill_total': str(sum(
                (
                    Decimal(tx.amount) for tx in report_transactions
                    if tx.status == 'Pending'
                ),
                Decimal('0.00')
            )),
            'total_bill_count': len(report_transactions),
            'services': sorted(services),
            'transactions': [{
                'id': tx.id,
                'date': tx.date,
                'customer_name': tx.customer_name,
                'category': tx.category,
                'amount': tx.amount,
                'status': tx.status,
                'payment_method': display_payment_method(tx.payment_method),
                'reference': tx.reference,
                'pax': tx.pax
            } for tx in report_transactions]
        }

    return {'error': 'Unknown portal section.'}, 404

@app.route('/expense_payment_receipt/<int:payment_id>')
def view_payment_receipt(payment_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    payment = db.session.get(PaymentHistory, payment_id)
    if not payment or not payment.receipt_file:
        return "Receipt not found.", 404
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        payment.receipt_file,
        as_attachment=False
    )

@app.route('/inventory/add', methods=['POST'])
def add_inventory_item():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    name = request.form.get('name', '').strip()
    category = request.form.get('category', 'Consumables').strip()
    subcategory = request.form.get('subcategory', 'Other').strip()
    unit_of_measure = request.form.get('unit_of_measure', 'Units').strip()
    raw_is_consumable = request.form.get('is_consumable', 'true').strip().lower()
    raw_reorder_level = request.form.get('reorder_level', '10').strip()
    raw_current_stock = request.form.get('current_stock', '0').strip()
    raw_unit_cost = request.form.get('unit_cost', '').strip()

    try:
        reorder_level = int(raw_reorder_level)
        current_stock = int(raw_current_stock)
    except ValueError:
        flash("Enter valid whole-number stock and reorder quantities.", "error")
        return redirect(url_for('inventory_dashboard'))

    try:
        unit_cost = (
            Decimal(raw_unit_cost).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if raw_unit_cost else None
        )
    except InvalidOperation:
        flash("Enter a valid unit cost.", "error")
        return redirect(url_for('inventory_dashboard'))

    if (
        not name or len(name) > 120
        or not category or len(category) > 50
        or not subcategory or len(subcategory) > 60
        or (
            category in INVENTORY_CATEGORIES
            and subcategory not in INVENTORY_CATEGORIES[category]
        )
        or not unit_of_measure or len(unit_of_measure) > 30
        or raw_is_consumable not in {'true', 'false'}
        or current_stock < 0 or reorder_level < 0
        or (
            unit_cost is not None and (
                not unit_cost.is_finite() or unit_cost < 0
                or unit_cost > Decimal('99999999.99')
            )
        )
        or (current_stock > 0 and (unit_cost is None or unit_cost <= 0))
    ):
        flash("Enter valid inventory details.", "error")
        return redirect(url_for('inventory_dashboard'))

    existing = InventoryItem.query.filter(
        db.func.lower(InventoryItem.name) == name.lower()
    ).first()
    if existing:
        flash("An inventory item with this name already exists.", "error")
        return redirect(url_for('inventory_dashboard'))

    item = InventoryItem(
        name=name,
        category=category,
        subcategory=subcategory,
        unit_of_measure=unit_of_measure,
        reorder_level=reorder_level,
        current_stock=current_stock,
        unit_cost=unit_cost,
        is_consumable=raw_is_consumable == 'true'
    )
    db.session.add(item)
    if current_stock > 0:
        db.session.flush()
        db.session.add(StockMovement(
            item_id=item.id,
            movement_type='IN',
            quantity=current_stock,
            reference='Initial Stock Opening',
            created_by=session.get('user')
        ))
    db.session.commit()

    flash(f"Added '{name}' to inventory!", "success")
    return redirect(url_for('inventory_dashboard'))

@app.route('/inventory/receive', methods=['POST'])
def receive_inventory_stock():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    item_id = request.form.get('item_id', type=int)
    quantity = request.form.get('quantity', type=int)
    reference = request.form.get('reference', '').strip()
    raw_unit_cost = request.form.get('unit_cost', '').strip()
    item = db.session.get(InventoryItem, item_id) if item_id else None
    if not item:
        flash("Select a valid inventory item.", "error")
        return redirect(url_for('inventory_dashboard'))

    try:
        unit_cost = Decimal(raw_unit_cost).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        flash("Enter a valid received unit cost.", "error")
        return redirect(url_for('inventory_dashboard'))
    if (
        not quantity or quantity <= 0 or len(reference) > 100 or not reference
        or not unit_cost.is_finite() or unit_cost <= 0
        or unit_cost > Decimal('99999999.99')
    ):
        flash(
            "Enter a positive quantity, valid unit cost, and supplier/invoice reference.",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    prior_stock = item.current_stock
    if prior_stock and item.unit_cost is not None:
        item.unit_cost = (
            (
                Decimal(item.unit_cost) * prior_stock + unit_cost * quantity
            ) / (prior_stock + quantity)
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        item.unit_cost = unit_cost
    item.current_stock += quantity
    db.session.add(StockMovement(
        item_id=item.id,
        movement_type='IN',
        quantity=quantity,
        reference=reference,
        created_by=session.get('user')
    ))
    db.session.commit()
    flash(f"Received {quantity} {item.unit_of_measure} of {item.name}.", "success")
    return redirect(url_for('inventory_dashboard'))

@app.route('/inventory/adjust', methods=['POST'])
def adjust_inventory_stock():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    item_id = request.form.get('item_id', type=int)
    counted_stock = request.form.get('counted_stock', type=int)
    reason = request.form.get('reason', '').strip()
    item = db.session.get(InventoryItem, item_id) if item_id else None
    if not item:
        flash("Select a valid inventory item.", "error")
        return redirect(url_for('inventory_dashboard'))
    if counted_stock is None or counted_stock < 0 or not reason or len(reason) > 500:
        flash("Enter a non-negative physical count and adjustment reason.", "error")
        return redirect(url_for('inventory_dashboard'))

    difference = counted_stock - item.current_stock
    if difference == 0:
        flash("Physical count matches the book balance; no adjustment was posted.", "success")
        return redirect(url_for('inventory_dashboard'))

    item.current_stock = counted_stock
    db.session.add(StockMovement(
        item_id=item.id,
        movement_type='ADJUSTMENT_IN' if difference > 0 else 'ADJUSTMENT_OUT',
        quantity=abs(difference),
        reference=reason,
        created_by=session.get('user')
    ))
    db.session.commit()
    flash(
        f"Stock adjusted by {difference:+d} {item.unit_of_measure} for {item.name}.",
        "success"
    )
    return redirect(url_for('inventory_dashboard'))

# --- TRACK 1: CONSUMABLE STORE REQUISITIONS ---
@app.route('/store/request', methods=['POST'])
def submit_requisition():
    if 'user' not in session:
        return redirect(url_for('login'))

    item_id = request.form.get('item_id', type=int)
    department_id = request.form.get('department_id', type=int)
    quantity = request.form.get('quantity', type=int)
    requester = request.form.get('requester_name', '').strip()

    item = db.session.get(InventoryItem, item_id) if item_id else None
    department = db.session.get(Department, department_id) if department_id else None
    if not item or not department:
        flash("Select a valid inventory item and department.", "error")
        return redirect(url_for('inventory_dashboard'))

    if not item.is_consumable:
        flash(
            "This is a durable asset. Use the Asset Transfer / Borrowing module instead.",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    if not quantity or quantity <= 0 or not requester or len(requester) > 100:
        flash("Enter a valid quantity and requester name.", "error")
        return redirect(url_for('inventory_dashboard'))

    db.session.add(StoreRequisition(
        item_id=item.id,
        department_id=department.id,
        quantity=quantity,
        requester_name=requester,
        status='PENDING',
        submitted_by=session.get('user')
    ))
    db.session.commit()
    flash("Store requisition submitted for admin approval.", "success")
    return redirect(url_for('inventory_dashboard'))

@app.route('/store/approve-issue/<int:req_id>', methods=['POST'])
def approve_and_issue_requisition(req_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    req = db.session.get(StoreRequisition, req_id)
    if not req:
        return "Requisition not found.", 404
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
        flash(
            f"Cannot issue. Insufficient stock! Available: {item.current_stock}",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    claimed = db.session.execute(
        update(StoreRequisition)
        .where(
            StoreRequisition.id == req.id,
            StoreRequisition.status == 'PENDING'
        )
        .values(status='ISSUED', issued_by=session.get('user'))
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("Requisition has already been processed.", "error")
        return redirect(url_for('inventory_dashboard'))

    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock >= req.quantity
        )
        .values(current_stock=InventoryItem.current_stock - req.quantity)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Cannot issue: stock changed or is insufficient.", "error")
        return redirect(url_for('inventory_dashboard'))
    db.session.expire(item, ['current_stock'])
    db.session.add(StockMovement(
        item_id=item.id,
        movement_type='OUT',
        quantity=req.quantity,
        department_id=req.department_id,
        reference=f"Requisition #{req.id}",
        created_by=session.get('user')
    ))
    db.session.commit()

    flash(
        f"Approved and issued {req.quantity} {item.unit_of_measure} "
        f"of {item.name} to {req.department.name}.",
        "success"
    )
    return redirect(url_for('inventory_dashboard'))

@app.route('/store/reject/<int:req_id>', methods=['POST'])
def reject_requisition(req_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    requisition = db.session.get(StoreRequisition, req_id)
    if not requisition:
        return "Requisition not found.", 404
    if requisition.status != 'PENDING':
        flash("Only pending requisitions can be rejected.", "error")
        return redirect(url_for('inventory_dashboard'))
    if requisition.submitted_by == session.get('user'):
        flash("A different administrator must reject this requisition.", "error")
        return redirect(url_for('inventory_dashboard'))
    reason = request.form.get('reason', '').strip()
    if not reason or len(reason) > 500:
        flash("Provide a rejection reason of 500 characters or fewer.", "error")
        return redirect(url_for('inventory_dashboard'))

    requisition.status = 'REJECTED'
    requisition.rejection_reason = reason
    requisition.rejected_by = session.get('user')
    db.session.commit()
    flash(f"Requisition #{req_id} rejected.", "success")
    return redirect(url_for('inventory_dashboard'))

# --- TRACK 2: DURABLE ASSET TRANSFER & RECOVERY WORKFLOW ---
@app.route('/assets/transfer', methods=['POST'])
def transfer_asset():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    item_id = request.form.get('item_id', type=int)
    department_id = request.form.get('department_id', type=int)
    quantity = request.form.get('quantity', type=int)
    borrower = request.form.get('borrower_name', '').strip()

    item = db.session.get(InventoryItem, item_id) if item_id else None
    department = db.session.get(Department, department_id) if department_id else None
    if not item or not department:
        flash("Select a valid inventory item and department.", "error")
        return redirect(url_for('inventory_dashboard'))

    if item.is_consumable:
        flash(
            "This item is marked as a consumable. Use store requisitions instead.",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    if not quantity or quantity <= 0 or not borrower or len(borrower) > 100:
        flash("Enter a valid quantity and borrower name.", "error")
        return redirect(url_for('inventory_dashboard'))
    if quantity > item.current_stock:
        flash(
            f"Asset transfer failed. Only {item.current_stock} available in store.",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock >= quantity
        )
        .values(current_stock=InventoryItem.current_stock - quantity)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Asset transfer failed: stock changed or is insufficient.", "error")
        return redirect(url_for('inventory_dashboard'))
    db.session.expire(item, ['current_stock'])
    tracking = AssetTracking(
        item_id=item.id,
        department_id=department.id,
        quantity=quantity,
        borrower_name=borrower,
        status='OUT',
        issued_by=session.get('user')
    )
    db.session.add(tracking)
    db.session.flush()
    db.session.add(StockMovement(
        item_id=item.id,
        movement_type='OUT',
        quantity=quantity,
        department_id=department.id,
        reference=f"Asset transfer #{tracking.id}",
        created_by=session.get('user')
    ))
    db.session.commit()

    flash(
        f"Asset transfer recorded: {quantity} {item.unit_of_measure} "
        f"{item.name} sent to {department.name}.",
        "success"
    )
    return redirect(url_for('inventory_dashboard'))

@app.route('/assets/return/<int:track_id>', methods=['POST'])
def return_asset(track_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    tracking = db.session.get(AssetTracking, track_id)
    if not tracking:
        return "Asset tracking record not found.", 404
    if tracking.status != 'OUT':
        flash("This asset tracking record is already closed.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.issued_by == session.get('user'):
        flash("A different administrator must verify the asset return.", "error")
        return redirect(url_for('inventory_dashboard'))

    condition = request.form.get('condition', 'GOOD')
    notes = request.form.get('inspection_notes', '').strip()
    if (
        condition not in {'GOOD', 'DAMAGED_LOST'} or len(notes) > 2000
        or (condition == 'DAMAGED_LOST' and not notes)
    ):
        flash("Enter a valid return condition and inspection note.", "error")
        return redirect(url_for('inventory_dashboard'))

    returned_at = datetime.utcnow()
    tracking_status = 'RETURNED_GOOD' if condition == 'GOOD' else 'DAMAGED_LOST'
    claimed = db.session.execute(
        update(AssetTracking)
        .where(
            AssetTracking.id == tracking.id,
            AssetTracking.status == 'OUT',
            or_(
                AssetTracking.issued_by.is_(None),
                AssetTracking.issued_by != session.get('user')
            )
        )
        .values(
            status=tracking_status,
            returned_at=returned_at,
            inspection_notes=notes or None,
            recovery_triggered=condition == 'DAMAGED_LOST',
            returned_by=session.get('user')
        )
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("This asset tracking record has already been closed.", "error")
        return redirect(url_for('inventory_dashboard'))

    if condition == 'GOOD':
        db.session.execute(
            update(InventoryItem)
            .where(InventoryItem.id == tracking.item_id)
            .values(current_stock=InventoryItem.current_stock + tracking.quantity)
        )
        db.session.add(StockMovement(
            item_id=tracking.item_id,
            movement_type='IN',
            quantity=tracking.quantity,
            department_id=tracking.department_id,
            reference=f"Asset return #{tracking.id}",
            created_by=session.get('user')
        ))
        flash("Asset returned in good condition and restocked.", "success")
    else:
        flash(
            f"Damage/loss recorded. Recovery workflow flagged for "
            f"{tracking.quantity} {tracking.item.name}.",
            "error"
        )

    db.session.commit()
    return redirect(url_for('inventory_dashboard'))

@app.route('/assets/recovery/<int:track_id>', methods=['POST'])
def create_asset_recovery_bill(track_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    tracking = db.session.get(AssetTracking, track_id)
    if not tracking:
        return "Asset tracking record not found.", 404
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

    recovery_amount = (
        Decimal(tracking.item.unit_cost) * tracking.quantity
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if not recovery_amount.is_finite() or recovery_amount > Decimal('999999999.99'):
        flash("The asset recovery estimate is outside the supported amount range.", "error")
        return redirect(url_for('inventory_dashboard'))

    expense_id = generate_prefixed_id('RECONCILIATION')
    recovery = Expense(
        id=expense_id,
        doc_type='Recovery',
        date_created=datetime.now().strftime('%Y-%m-%d %H:%M'),
        caretaker_name=tracking.borrower_name,
        item_description=(
            f"Book-value recovery estimate for {tracking.quantity} "
            f"{tracking.item.unit_of_measure} {tracking.item.name} "
            f"(asset record #{tracking.id})"
        ),
        estimated_amount=f"{recovery_amount:.2f}",
        actual_amount='0.00',
        status='Pending Approval',
        logged_by=session.get('user'),
        audit_ref=f"AST-{tracking.id}",
        linked_asset_tracking_id=tracking.id
    )
    claimed = db.session.execute(
        update(AssetTracking)
        .where(
            AssetTracking.id == tracking.id,
            AssetTracking.recovery_expense_id.is_(None),
            AssetTracking.status == 'DAMAGED_LOST'
        )
        .values(recovery_expense_id=expense_id)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("A recovery document was already created for this record.", "error")
        return redirect(url_for('inventory_dashboard'))
    db.session.add(recovery)
    db.session.commit()
    flash(
        f"Recovery document {expense_id} created using book value as an estimate; "
        "confirm the amount against replacement/repair evidence before payment.",
        "success"
    )
    return redirect(url_for('expenses_panel'))

# --- EXPENSES & LPO ROUTES ---
@app.route('/expenses')
def expenses_panel():
    if 'user' not in session:
        return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    
    all_expenses = get_expenses()
    doc_filter = request.args.get('type', 'ALL').upper()
    expenses = [e for e in all_expenses if doc_filter == 'ALL' or e.doc_type.upper() == doc_filter]
    
    now = datetime.now()
    all_payments = PaymentHistory.query.order_by(PaymentHistory.id.asc()).all()
    payments_by_expense = {}
    for payment in all_payments:
        payments_by_expense.setdefault(payment.expense_id, []).append(payment)
    spend_month = sum(
        (
            Decimal(payment.amount) for payment in all_payments
            if payment.date.startswith(now.strftime('%Y-%m'))
        ),
        Decimal('0.00')
    )
    spend_year = sum(
        (
            Decimal(payment.amount) for payment in all_payments
            if payment.date.startswith(now.strftime('%Y'))
        ),
        Decimal('0.00')
    )
    total_pending = [
        expense for expense in all_expenses
        if expense.status in {
            'Pending Approval', 'Pending Purchase', 'Partially Paid'
        }
    ]

    return render_template(
        'expenses.html', 
        expenses=[{
            'id': e.id, 'doc_type': e.doc_type, 'date_created': e.date_created,
            'caretaker_name': e.caretaker_name, 'item_description': e.item_description,
            'estimated_amount': e.estimated_amount, 'actual_amount': e.actual_amount,
            'invoice_ref': e.invoice_ref, 'receipt_file': e.receipt_file,
            'receipt_note': e.receipt_note, 'status': e.status,
            'logged_by': e.logged_by, 'approved_by': e.approved_by,
            'rejected_by': e.rejected_by
        } for e in expenses], 
        total_pending_count=len(total_pending),
        total_pending_amount=sum(
            (
                max(
                    Decimal(e.estimated_amount) - Decimal(e.actual_amount),
                    Decimal('0.00')
                )
                for e in total_pending
            ),
            Decimal('0.00')
        ),
        total_fulfilled_count=sum(
            1 for e in all_expenses if e.status == 'Fulfilled'
        ),
        spend_year=spend_year,
        all_count=len(all_expenses),
        lpo_count=sum(1 for e in all_expenses if e.doc_type == 'LPO'),
        bill_count=sum(1 for e in all_expenses if e.doc_type == 'Bill'),
        recovery_count=sum(1 for e in all_expenses if e.doc_type == 'Recovery'),
        current_filter=doc_filter,
        payments_by_expense=payments_by_expense,
        users=get_users(), 
        spend_month=spend_month,
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/procurement_analytics')
def procurement_analytics():
    if 'user' not in session:
        return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')

    expenses = get_expenses()
    total_committed = sum(
        (
            Decimal(expense.estimated_amount)
            for expense in expenses
            if expense.doc_type == 'LPO'
            and expense.status not in {'Pending Approval', 'Rejected'}
        ),
        Decimal('0.00')
    )
    total_savings = Decimal('0.00')
    total_overpay = Decimal('0.00')
    total_partial = Decimal('0.00')
    status_counts = {
        'Fulfilled': 0,
        'Partially Paid': 0,
        'Pending': 0,
        'Pending Approval': 0,
        'Rejected': 0
    }
    cost_centers = {}
    records = []
    for expense in expenses:
        estimated = Decimal(expense.estimated_amount)
        actual = Decimal(expense.actual_amount)
        if expense.status == 'Fulfilled':
            status_counts['Fulfilled'] += 1
            variance = estimated - actual
            if variance > 0:
                total_savings += variance
                variance_type = 'Favorable Savings'
            elif variance < 0:
                total_overpay += abs(variance)
                variance_type = 'Overpayment'
            else:
                variance_type = 'Exact Match'
        elif expense.status == 'Partially Paid':
            status_counts['Partially Paid'] += 1
            balance = max(estimated - actual, Decimal('0.00'))
            total_partial += balance
            variance_type = 'Partial Balance Due'
        elif expense.status == 'Pending Approval':
            status_counts['Pending Approval'] += 1
            variance_type = None
        elif expense.status == 'Rejected':
            status_counts['Rejected'] += 1
            variance_type = None
        else:
            status_counts['Pending'] += 1
            variance_type = None

        cost_centers[expense.caretaker_name] = (
            cost_centers.get(expense.caretaker_name, Decimal('0.00')) + actual
        )
        if variance_type:
            variance_amount = (
                abs(estimated - actual)
                if expense.status == 'Fulfilled'
                else max(estimated - actual, Decimal('0.00'))
            )
            records.append({
                'id': expense.id,
                'date': expense.date_created,
                'item': expense.item_description,
                'audit_ref': expense.audit_ref,
                'invoice_ref': expense.invoice_ref,
                'variance_type': variance_type,
                'variance_amount': variance_amount
            })

    return render_template(
        'procurement_analytics.html',
        total_committed=total_committed,
        total_savings=total_savings,
        total_overpay=total_overpay,
        total_partial=total_partial,
        sorted_cost_centers=sorted(
            cost_centers.items(), key=lambda item: item[1], reverse=True
        ),
        status_counts=status_counts,
        records=records
    )

@app.route('/create_expense_order', methods=['POST'])
def create_expense_order():
    if 'user' not in session:
        return redirect(url_for('login'))
        
    doc_type = request.form.get('doc_type', 'LPO').strip().upper()
    caretaker_name = request.form.get('caretaker_name', '').strip()
    item_description = request.form.get('item_description', '').strip()
    try:
        amount = Decimal(request.form.get('amount', '')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        flash("Enter a valid estimated amount.", "error")
        return redirect(url_for('expenses_panel'))

    if (
        doc_type not in {'LPO', 'BILL'}
        or not caretaker_name or len(caretaker_name) > 100
        or not item_description or len(item_description) > 2000
        or not amount.is_finite() or amount <= 0
        or amount > Decimal('999999999.99')
    ):
        flash("Enter a valid document type, buyer, description, and amount.", "error")
        return redirect(url_for('expenses_panel'))

    stored_doc_type = 'LPO' if doc_type == 'LPO' else 'Bill'
    new_id = generate_prefixed_id('LPO' if doc_type == 'LPO' else 'REQUISITION')
    new_exp = Expense(
        id=new_id,
        doc_type=stored_doc_type,
        date_created=datetime.now().strftime('%Y-%m-%d %H:%M'),
        caretaker_name=caretaker_name,
        item_description=item_description,
        estimated_amount=f"{amount:.2f}",
        actual_amount='0.00',
        status='Pending Approval',
        logged_by=session.get('user')
    )
    db.session.add(new_exp)
    db.session.commit()
    flash(f"{stored_doc_type} {new_id} submitted for approval.", "success")
    return redirect(url_for('expenses_panel'))

@app.route('/approve_expense/<exp_id>', methods=['POST'])
def approve_expense(exp_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    expense = db.session.get(Expense, exp_id)
    if not expense:
        return "Expense record not found.", 404
    if expense.status != 'Pending Approval':
        flash("Only documents pending approval can be approved.", "error")
        return redirect(url_for('expenses_panel'))
    if expense.logged_by == session.get('user'):
        flash("A different administrator must approve this document.", "error")
        return redirect(url_for('expenses_panel'))

    approved_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    approved = db.session.execute(
        update(Expense)
        .where(
            Expense.id == expense.id,
            Expense.status == 'Pending Approval',
            or_(Expense.logged_by.is_(None), Expense.logged_by != session.get('user'))
        )
        .values(
            status='Pending Purchase',
            approved_by=session.get('user'),
            approved_at=approved_at
        )
    )
    if approved.rowcount != 1:
        db.session.rollback()
        flash("This document was already processed.", "error")
        return redirect(url_for('expenses_panel'))
    db.session.commit()
    flash(f"Document {exp_id} approved for purchase.", "success")
    return redirect(url_for('expenses_panel'))

@app.route('/reject_expense/<exp_id>', methods=['POST'])
def reject_expense(exp_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    expense = db.session.get(Expense, exp_id)
    if not expense:
        return "Expense record not found.", 404
    if expense.status != 'Pending Approval':
        flash("Only documents pending approval can be rejected.", "error")
        return redirect(url_for('expenses_panel'))
    if expense.logged_by == session.get('user'):
        flash("A different administrator must reject this document.", "error")
        return redirect(url_for('expenses_panel'))
    reason = request.form.get('reason', '').strip()
    if not reason or len(reason) > 500:
        flash("Provide a rejection reason of 500 characters or fewer.", "error")
        return redirect(url_for('expenses_panel'))

    rejected = db.session.execute(
        update(Expense)
        .where(
            Expense.id == expense.id,
            Expense.status == 'Pending Approval',
            or_(Expense.logged_by.is_(None), Expense.logged_by != session.get('user'))
        )
        .values(
            status='Rejected',
            rejected_by=session.get('user'),
            rejection_reason=reason
        )
    )
    if rejected.rowcount != 1:
        db.session.rollback()
        flash("This document was already processed.", "error")
        return redirect(url_for('expenses_panel'))
    db.session.commit()
    flash(f"Document {exp_id} rejected.", "success")
    return redirect(url_for('expenses_panel'))

@app.route('/fulfill_expense/<exp_id>', methods=['POST'])
def fulfill_expense(exp_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    exp = db.session.get(Expense, exp_id)
    if not exp:
        return "Expense record not found.", 404

    if exp.status not in {'Pending Purchase', 'Partially Paid'}:
        flash(f"Expense {exp_id} is not awaiting payment.", "error")
        return redirect(url_for('expenses_panel'))
    if exp.logged_by == session.get('user'):
        flash("A different administrator must record payment for this document.", "error")
        return redirect(url_for('expenses_panel'))

    invoice_ref = request.form.get('invoice_ref', '').strip()
    receipt_note = request.form.get('service_ref_note', '').strip()
    variance_reason = request.form.get('variance_reason', '').strip()
    upload = request.files.get('receipt_file')
    if len(invoice_ref) > 50 or len(receipt_note) > 500 or len(variance_reason) > 500:
        flash("Payment references and notes exceed the allowed length.", "error")
        return redirect(url_for('expenses_panel'))

    receipt_filename = None
    if upload and upload.filename:
        safe_original_name = secure_filename(upload.filename)
        if not safe_original_name or not allowed_file(safe_original_name):
            flash("Receipt must be a PDF, PNG, JPG, or JPEG file.", "error")
            return redirect(url_for('expenses_panel'))
        if not upload.stream.read(1):
            flash("The receipt file cannot be empty.", "error")
            return redirect(url_for('expenses_panel'))
        upload.stream.seek(0)
        extension = safe_original_name.rsplit('.', 1)[1].lower()
        receipt_filename = f"{secrets.token_hex(16)}.{extension}"

    if not invoice_ref and not receipt_note and not receipt_filename:
        flash("Provide an invoice/payment reference, receipt file, or service note.", "error")
        return redirect(url_for('expenses_panel'))
    if invoice_ref and PaymentHistory.query.filter(
        db.func.lower(PaymentHistory.invoice_ref) == invoice_ref.lower()
    ).first():
        flash("This invoice/payment reference has already been recorded.", "error")
        return redirect(url_for('expenses_panel'))

    try:
        payment_amount = Decimal(
            request.form.get('actual_amount', '')
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        flash("Payment amount must be a valid number.", "error")
        return redirect(url_for('expenses_panel'))
    if (
        not payment_amount.is_finite() or payment_amount <= 0
        or payment_amount > Decimal('999999999.99')
    ):
        flash("Payment amount must be a valid positive amount.", "error")
        return redirect(url_for('expenses_panel'))

    payments = PaymentHistory.query.filter_by(expense_id=exp.id).all()
    history_paid = sum(
        (Decimal(payment.amount) for payment in payments), Decimal('0.00')
    )
    try:
        recorded_paid = Decimal(exp.actual_amount)
        estimated_amount = Decimal(exp.estimated_amount)
    except InvalidOperation:
        flash("This document has invalid stored amounts and cannot be paid.", "error")
        return redirect(url_for('expenses_panel'))
    if (
        not recorded_paid.is_finite() or recorded_paid < 0
        or not estimated_amount.is_finite() or estimated_amount <= 0
    ):
        flash("This document has invalid stored amounts and cannot be paid.", "error")
        return redirect(url_for('expenses_panel'))
    if history_paid != recorded_paid:
        app.logger.warning(
            "Expense %s has recorded paid total %s but payment history totals %s",
            exp.id, recorded_paid, history_paid
        )
    already_paid = max(recorded_paid, history_paid)
    total_actual = already_paid + payment_amount
    if total_actual > estimated_amount and not variance_reason:
        flash("Explain the cost overrun before recording this payment.", "error")
        return redirect(url_for('expenses_panel'))

    payment_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    payment_status = 'Fulfilled' if total_actual >= estimated_amount else 'Partially Paid'
    updated = db.session.execute(
        update(Expense)
        .where(
            Expense.id == exp.id,
            Expense.status.in_({'Pending Purchase', 'Partially Paid'}),
            Expense.actual_amount == exp.actual_amount,
            or_(
                Expense.logged_by.is_(None),
                Expense.logged_by != session.get('user')
            )
        )
        .values(
            actual_amount=f"{total_actual:.2f}",
            invoice_ref=invoice_ref or 'N/A',
            receipt_file=receipt_filename or 'N/A',
            receipt_note=receipt_note or None,
            fulfilled_by=session.get('user'),
            last_payment_date=payment_date,
            last_payment_by=session.get('user'),
            status=payment_status
        )
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("This document was updated by another administrator. Reload before recording payment.", "error")
        return redirect(url_for('expenses_panel'))

    if receipt_filename:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        upload.save(os.path.join(app.config['UPLOAD_FOLDER'], receipt_filename))

    db.session.add(PaymentHistory(
        expense_id=exp.id,
        amount=f"{payment_amount:.2f}",
        date=payment_date,
        paid_by=session.get('user'),
        invoice_ref=invoice_ref or None,
        receipt_file=receipt_filename,
        receipt_note=receipt_note or None,
        variance_reason=variance_reason or None
    ))
    if exp.linked_transfer_id and payment_status == 'Fulfilled':
        transfer = db.session.get(AssetTransfer, exp.linked_transfer_id)
        if transfer:
            transfer.status = 'Resolved & Paid'
    db.session.commit()
    flash(f"Payment recorded for {exp_id}.", "success")
    return redirect(url_for('expenses_panel'))

@app.route('/lpo/<exp_id>')
def view_lpo(exp_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    exp = Expense.query.get(exp_id)
    if not exp:
        return "Document not found", 404
    if exp.status in {'Pending Approval', 'Rejected'}:
        flash("Only approved procurement documents can be printed.", "error")
        return redirect(url_for('expenses_panel'))
    return render_template('lpo_print.html', exp=exp)

@app.route('/lpo_thermal/<exp_id>')
def view_lpo_thermal(exp_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    exp = Expense.query.get(exp_id)
    if not exp:
        return "Document not found", 404
    if exp.status in {'Pending Approval', 'Rejected'}:
        flash("Only approved procurement documents can be printed.", "error")
        return redirect(url_for('expenses_panel'))
    return render_template('lpo_thermal.html', exp=exp)

@app.route('/cash_voucher/<exp_id>')
def view_cash_voucher(exp_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    exp = Expense.query.get(exp_id)
    if not exp:
        return "Expense record not found", 404
    if exp.status in {'Pending Approval', 'Rejected'}:
        flash("Only approved expenses can have a cash voucher.", "error")
        return redirect(url_for('expenses_panel'))
    return render_template('cash_voucher.html', expense=exp)

# --- ASSET TRANSFERS ROUTES ---
@app.route('/transfers')
def transfers_panel():
    if 'user' not in session:
        return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    today = datetime.now().date()
    transfer_views = []
    overdue_count = 0
    discrepancy_count = 0
    for transfer in get_transfers():
        try:
            is_overdue = (
                transfer.status == 'Active'
                and bool(transfer.return_date)
                and datetime.strptime(transfer.return_date, '%Y-%m-%d').date() < today
            )
        except ValueError:
            is_overdue = False
        overdue_count += int(is_overdue)
        discrepancy_count += int(transfer.status == 'Discrepancy')
        transfer_views.append(TemplateRecord({
            'id': transfer.id,
            'date_sent': transfer.date_sent,
            'origin': transfer.origin,
            'destination': transfer.destination,
            'requested_by': transfer.requested_by,
            'approved_by': transfer.approved_by,
            'reconciled_by': transfer.reconciled_by,
            'custodian': transfer.custodian,
            'return_date': transfer.return_date,
            'status': transfer.status,
            'discrepancy_notes': transfer.discrepancy_notes,
            'recovery_bill_id': transfer.recovery_bill_id,
            'reconciled_by': transfer.reconciled_by,
            'is_overdue': is_overdue,
            'items': [
                TemplateRecord({
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing
                })
                for item in TransferItem.query.filter_by(
                    transfer_id=transfer.id
                ).all()
            ]
        }))
    return render_template(
        'transfers.html',
        transfers=transfer_views,
        active_count=sum(1 for t in transfer_views if t.status == 'Active'),
        overdue_count=overdue_count,
        discrepancy_count=discrepancy_count,
        users=get_users(),
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/create_transfer', methods=['POST'])
def create_transfer():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    raw_item_ids = request.form.getlist('item_id[]')
    quantities = request.form.getlist('qty_sent[]')
    if not all(request.form.get(key, '').strip() for key in (
        'destination', 'requested_by', 'custodian', 'return_date'
    )) or not raw_item_ids:
        flash("Complete the transfer details and add at least one item.", "error")
        return redirect(url_for('transfers_panel'))
    return_date = request.form.get('return_date', '').strip()
    try:
        parsed_return_date = datetime.strptime(return_date, '%Y-%m-%d').date()
        parsed_item_ids = [int(item_id) for item_id in raw_item_ids]
        parsed_quantities = [int(qty) for qty in quantities]
    except ValueError:
        flash("Select valid inventory items and enter valid quantities.", "error")
        return redirect(url_for('transfers_panel'))
    if len(parsed_item_ids) != len(parsed_quantities) or any(
        item_id <= 0 for item_id in parsed_item_ids
    ) or len(parsed_item_ids) != len(set(parsed_item_ids)) or any(
        quantity < 1 for quantity in parsed_quantities
    ):
        flash("Choose each inventory item once and use quantities greater than zero.", "error")
        return redirect(url_for('transfers_panel'))
    if parsed_return_date < datetime.now().date() or any(
        len(request.form.get(field, '').strip()) > limit
        for field, limit in (
            ('origin', 100), ('destination', 100),
            ('requested_by', 100), ('custodian', 100)
        )
    ):
        flash("Enter a future return date and valid transfer details.", "error")
        return redirect(url_for('transfers_panel'))

    inventory_items = {
        item.id: item for item in InventoryItem.query.filter(
            InventoryItem.id.in_(parsed_item_ids),
            InventoryItem.is_consumable.is_(False)
        ).all()
    }
    if len(inventory_items) != len(parsed_item_ids):
        flash("Select valid durable inventory items.", "error")
        return redirect(url_for('transfers_panel'))
    if any(
        quantity > inventory_items[item_id].current_stock
        for item_id, quantity in zip(parsed_item_ids, parsed_quantities)
    ):
        flash("A selected item has insufficient available stock.", "error")
        return redirect(url_for('transfers_panel'))

    new_id = generate_prefixed_id('TRANSFER')
    new_trf = AssetTransfer(
        id=new_id,
        date_sent=datetime.now().strftime('%Y-%m-%d %H:%M'),
        origin=request.form.get('origin', 'Main Store'),
        destination=request.form.get('destination', ''),
        requested_by=request.form.get('requested_by', ''),
        approved_by=session.get('user'),
        custodian=request.form.get('custodian', ''),
        return_date=request.form.get('return_date', ''),
        status='Active'
    )
    db.session.add(new_trf)
    db.session.flush()

    for item_id, quantity in zip(parsed_item_ids, parsed_quantities):
        item = inventory_items[item_id]
        updated = db.session.execute(
            update(InventoryItem)
            .where(
                InventoryItem.id == item_id,
                InventoryItem.is_consumable.is_(False),
                InventoryItem.current_stock >= quantity
            )
            .values(current_stock=InventoryItem.current_stock - quantity)
        )
        if updated.rowcount != 1:
            db.session.rollback()
            flash("Transfer failed because stock changed or is insufficient.", "error")
            return redirect(url_for('transfers_panel'))
        db.session.add(TransferItem(
            transfer_id=new_id,
            item_id=item_id,
            name=item.name,
            sent=quantity
        ))
        db.session.add(StockMovement(
            item_id=item_id,
            movement_type='OUT',
            quantity=quantity,
            reference=f"Transfer {new_id}",
            created_by=session.get('user')
        ))

    db.session.commit()
    flash(f"✅ Transfer {new_id} logged successfully.", "success")
    return redirect(url_for('transfers_panel'))

@app.route('/transfer_slip/<transfer_id>')
def view_transfer_slip(transfer_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    transfer = AssetTransfer.query.get(transfer_id)
    if not transfer:
        return "Transfer not found", 404
    return render_template(
        'transfer_slip.html',
        trf=TemplateRecord({
            'id': transfer.id,
            'date_sent': transfer.date_sent,
            'origin': transfer.origin,
            'destination': transfer.destination,
            'requested_by': transfer.requested_by,
            'approved_by': transfer.approved_by,
            'custodian': transfer.custodian,
            'return_date': transfer.return_date,
            'items': [
                TemplateRecord({
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing
                })
                for item in TransferItem.query.filter_by(
                    transfer_id=transfer.id
                ).all()
            ]
        })
    )

@app.route('/transfer_return_slip/<transfer_id>')
def view_transfer_return_slip(transfer_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    transfer = AssetTransfer.query.get(transfer_id)
    if not transfer:
        return "Transfer not found", 404
    return render_template(
        'transfer_return_slip.html',
        trf=TemplateRecord({
            'id': transfer.id,
            'origin': transfer.origin,
            'destination': transfer.destination,
            'requested_by': transfer.requested_by,
            'custodian': transfer.custodian,
            'status': transfer.status,
            'discrepancy_notes': transfer.discrepancy_notes,
            'recovery_bill_id': transfer.recovery_bill_id,
            'items': [
                TemplateRecord({
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing
                })
                for item in TransferItem.query.filter_by(
                    transfer_id=transfer.id
                ).all()
            ]
        })
    )

@app.route('/return_transfer/<transfer_id>', methods=['POST'])
def return_transfer(transfer_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    transfer = AssetTransfer.query.get(transfer_id)
    if not transfer:
        return "Transfer not found", 404
    if transfer.status in {'Fully Returned', 'Resolved & Paid'} or (
        transfer.status == 'Discrepancy' and transfer.recovery_bill_id
    ):
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
            if (
                f'good_{index}' not in request.form
                or f'damaged_{index}' not in request.form
            ):
                raise ValueError
            good = int(request.form.get(f'good_{index}', '0'))
            damaged = int(request.form.get(f'damaged_{index}', '0'))
            if good < 0 or damaged < 0 or good + damaged > item.sent:
                raise ValueError
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
        AssetTransfer.id == transfer.id,
        AssetTransfer.status == current_status,
        or_(
            AssetTransfer.approved_by.is_(None),
            AssetTransfer.approved_by != session.get('user')
        ),
        or_(
            AssetTransfer.reconciled_by.is_(None),
            AssetTransfer.reconciled_by != session.get('user')
        )
    ]
    claimed = db.session.execute(
        update(AssetTransfer)
        .where(*update_conditions)
        .values(
            status=new_status,
            discrepancy_notes=discrepancy_notes or 'N/A',
            reconciled_by=session.get('user')
        )
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
            db.session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == item.item_id)
                .values(current_stock=InventoryItem.current_stock + good)
            )
            db.session.add(StockMovement(
                item_id=item.item_id,
                movement_type='IN',
                quantity=good,
                reference=f"Transfer return {transfer.id}",
                created_by=session.get('user')
            ))
    db.session.commit()
    flash(f"Return details saved for transfer {transfer.id}.", "success")
    return redirect(url_for('transfers_panel'))

@app.route('/create_recovery_bill/<transfer_id>', methods=['POST'])
def create_recovery_bill(transfer_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    transfer = AssetTransfer.query.get(transfer_id)
    if not transfer:
        return "Transfer not found", 404
    if transfer.status != 'Discrepancy' or transfer.recovery_bill_id:
        flash("A recovery bill can only be created once for an unresolved discrepancy.", "error")
        return redirect(url_for('transfers_panel'))
    if (
        not transfer.discrepancy_notes
        or transfer.discrepancy_notes == 'N/A'
        or transfer.reconciled_by == session.get('user')
    ):
        flash(
            "A different administrator and a documented discrepancy are required for recovery billing.",
            "error"
        )
        return redirect(url_for('transfers_panel'))

    names = request.form.getlist('item_names[]')
    prices = request.form.getlist('unit_prices[]')
    items_by_name = {
        item.name: item for item in TransferItem.query.filter_by(
            transfer_id=transfer.id
        ).all()
    }
    discrepancy_names = {
        item.name for item in items_by_name.values()
        if item.damaged + item.missing > 0
    }
    if (
        not names or len(names) != len(prices)
        or len(names) != len(set(names))
        or set(names) != discrepancy_names
    ):
        flash("Provide a unit price for each damaged or missing item.", "error")
        return redirect(url_for('transfers_panel'))
    try:
        total_amount = Decimal('0.00')
        for name, raw_price in zip(names, prices):
            item = items_by_name.get(name)
            unit_price = Decimal(raw_price).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if not item or (item.damaged + item.missing) <= 0 or not unit_price.is_finite() or unit_price <= 0:
                raise ValueError
            total_amount += unit_price * (item.damaged + item.missing)
            if total_amount > Decimal('999999999.99'):
                raise ValueError
    except (InvalidOperation, ValueError):
        flash("Recovery prices must be valid positive amounts for discrepant items.", "error")
        return redirect(url_for('transfers_panel'))

    expense_id = generate_prefixed_id('RECONCILIATION')
    recovery = Expense(
        id=expense_id,
        doc_type='Recovery',
        date_created=datetime.now().strftime('%Y-%m-%d %H:%M'),
        caretaker_name=transfer.custodian,
        item_description=f"Asset recovery for transfer {transfer.id}",
        estimated_amount=f"{total_amount:.2f}",
        actual_amount='0.00',
        status='Pending Approval',
        logged_by=session.get('user'),
        linked_transfer_id=transfer.id
    )
    claimed = db.session.execute(
        update(AssetTransfer)
        .where(
            AssetTransfer.id == transfer.id,
            AssetTransfer.status == 'Discrepancy',
            AssetTransfer.recovery_bill_id.is_(None),
            or_(
                AssetTransfer.reconciled_by.is_(None),
                AssetTransfer.reconciled_by != session.get('user')
            )
        )
        .values(recovery_bill_id=expense_id)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("A recovery document was already created for this transfer.", "error")
        return redirect(url_for('transfers_panel'))
    db.session.add(recovery)
    db.session.commit()
    flash(f"Recovery bill {expense_id} generated.", "success")
    return redirect(url_for('expenses_panel'))

# --- HR & ATTENDANCE ROUTES ---
@app.route('/hr/attendance', methods=['GET', 'POST'])
def daily_attendance():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'GET' and os.path.isfile(
        os.path.join(app.static_folder, 'react', 'assets', 'index.js')
    ):
        return render_template('react_app.html')

    staff_list = Staff.query.all()
    max_date = datetime.now().strftime('%Y-%m-%d')
    selected_date = (
        request.form.get('date') if request.method == 'POST'
        else request.args.get('date', max_date)
    ) or max_date
    selected_end_date = (
        request.form.get('end_date') if request.method == 'POST'
        else request.args.get('end_date', selected_date)
    ) or selected_date

    if request.method == 'POST':
        end_date = selected_end_date
        if not is_valid_iso_date(selected_date):
            flash("Enter a valid attendance date in YYYY-MM-DD format.", "error")
            return redirect(url_for('daily_attendance', date=max_date))
        if not is_valid_iso_date(end_date):
            flash("Enter a valid end date in YYYY-MM-DD format.", "error")
            return redirect(url_for('daily_attendance', date=selected_date))
        if selected_date > max_date or end_date > max_date:
            flash("You cannot mark attendance for future dates!", "error")
            return redirect(url_for('daily_attendance', date=max_date))
        start_day = datetime.strptime(selected_date, '%Y-%m-%d').date()
        end_day = datetime.strptime(end_date, '%Y-%m-%d').date()
        if end_day < start_day:
            flash("The end date cannot be earlier than the start date.", "error")
            return redirect(url_for('daily_attendance', date=selected_date))
        if (end_day - start_day).days >= 31:
            flash("Attendance can be marked for a maximum of 31 days at a time.", "error")
            return redirect(url_for('daily_attendance', date=selected_date))

        attendance_dates = [
            (start_day + timedelta(days=offset)).isoformat()
            for offset in range((end_day - start_day).days + 1)
        ]

        existing_date = Attendance.query.filter(
            Attendance.date.in_(attendance_dates)
        ).with_entities(Attendance.date).first()
        if existing_date:
            flash(
                f"Attendance for {existing_date[0]} has already been marked. "
                "No dates in this range were saved.",
                "error"
            )
            return redirect(url_for(
                'daily_attendance', date=selected_date, end_date=end_date
            ))
        if not staff_list:
            flash("Add staff before marking attendance.", "error")
            return redirect(url_for(
                'daily_attendance', date=selected_date, end_date=end_date
            ))

        staff_attendance = []
        for staff in staff_list:
            status = request.form.get(f'status_{staff.id}', '')
            hours_value = request.form.get(f'hours_{staff.id}', '')
            try:
                hours = float(hours_value)
            except (TypeError, ValueError):
                flash(f"Enter valid hours for {staff.name}.", "error")
                return redirect(url_for(
                    'daily_attendance', date=selected_date, end_date=end_date
                ))
            if (
                status not in {'PRESENT', 'ABSENT', 'LATE', 'LEAVE'}
                or not math.isfinite(hours) or not 0 <= hours <= 24
            ):
                flash(f"Enter a valid attendance status and hours for {staff.name}.", "error")
                return redirect(url_for(
                    'daily_attendance', date=selected_date, end_date=end_date
                ))
            staff_attendance.append((
                staff.id,
                status,
                hours,
                request.form.get(f'off_day_{staff.id}') == 'on',
                request.form.get(f'holiday_{staff.id}') == 'on',
                request.form.get(f'notes_{staff.id}', '').strip()
            ))
        entries = [
            Attendance(
                date=attendance_date,
                staff_id=staff_id,
                status=status,
                hours_worked=hours,
                is_off_day_work=is_off_day_work,
                is_holiday_work=is_holiday_work,
                notes=notes
            )
            for attendance_date in attendance_dates
            for (
                staff_id, status, hours, is_off_day_work,
                is_holiday_work, notes
            ) in staff_attendance
        ]
        db.session.add_all(entries)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(
                "Attendance for at least one date was already saved. "
                "No dates in this range were changed.",
                "error"
            )
            return redirect(url_for(
                'daily_attendance', date=selected_date, end_date=end_date
            ))
        flash(
            f"Attendance for {selected_date} through {end_date} saved successfully!",
            "success"
        )
        return redirect(url_for(
            'daily_attendance', date=selected_date, end_date=end_date
        ))

    if not is_valid_iso_date(selected_date):
        return "Date must use YYYY-MM-DD format.", 400
    if not is_valid_iso_date(selected_end_date):
        return "End date must use YYYY-MM-DD format.", 400
    start_day = datetime.strptime(selected_date, '%Y-%m-%d').date()
    end_day = datetime.strptime(selected_end_date, '%Y-%m-%d').date()
    if end_day < start_day or selected_end_date > max_date:
        return "Attendance date range is invalid.", 400
    if (end_day - start_day).days >= 31:
        return "Attendance can be viewed for a maximum of 31 days at a time.", 400

    existing_log = {a.staff_id: {'status': a.status, 'hours_worked': a.hours_worked, 'is_off_day_work': a.is_off_day_work, 'is_holiday_work': a.is_holiday_work, 'notes': a.notes} for a in Attendance.query.filter_by(date=selected_date).all()}
    attendance_marked = Attendance.query.filter(
        Attendance.date >= selected_date,
        Attendance.date <= selected_end_date
    ).first() is not None

    return render_template(
        'hr_attendance.html',
        staff_list=[{'id': s.id, 'name': s.name, 'department': s.department} for s in staff_list],
        selected_date=selected_date,
        selected_end_date=selected_end_date,
        max_date=max_date,
        existing_log=existing_log,
        attendance_marked=attendance_marked,
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/hr/staff/add', methods=['GET', 'POST'])
def add_staff():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        department = request.form.get('department', '').strip()
        if name and department:
            count = Staff.query.count() + 1
            new_id = f"STF{count:03d}"
            db.session.add(Staff(id=new_id, name=name, department=department))
            db.session.commit()
            flash(f"Staff member {name} added successfully!", "success")
            return redirect(url_for('daily_attendance'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template('add_staff.html', user=session.get('user'), role=session.get('role'))

@app.route('/hr/report', methods=['GET'])
def hr_payroll_report():
    if 'user' not in session:
        return redirect(url_for('login'))
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    dept_filter = request.args.get('department', 'ALL')
    if start_date or end_date:
        if not is_valid_iso_date(start_date) or not is_valid_iso_date(end_date):
            return "Start and end dates must use YYYY-MM-DD format.", 400
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        if end < start:
            return "End date cannot be earlier than start date.", 400

    report_data = []
    staff_query = Staff.query
    if dept_filter != 'ALL':
        staff_query = staff_query.filter_by(department=dept_filter)

    for staff in staff_query.all():
        logs = Attendance.query.filter(Attendance.staff_id == staff.id, Attendance.date >= start_date, Attendance.date <= end_date).all() if start_date and end_date else []
        days_worked = sum(1 for l in logs if l.status != 'ABSENT')
        total_hours = sum(l.hours_worked for l in logs if l.status != 'ABSENT')
        report_data.append({
            'staff': {'id': staff.id, 'name': staff.name, 'department': staff.department},
            'days_worked': days_worked,
            'off_day_work': sum(1 for l in logs if l.is_off_day_work),
            'holiday_work': sum(1 for l in logs if l.is_holiday_work),
            'total_hours': total_hours,
            'overtime_hours': sum(max(0.0, l.hours_worked - 8) for l in logs if l.status != 'ABSENT'),
            'undertime_hours': sum(max(0.0, 8 - l.hours_worked) for l in logs if l.status == 'PRESENT' and l.hours_worked < 8)
        })

    departments = [d[0] for d in db.session.query(Staff.department).distinct().all()]
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template('hr_report.html', report_data=report_data, start_date=start_date, end_date=end_date, dept_filter=dept_filter, departments=departments, user=session.get('user'), role=session.get('role'))

def get_staff_attendance_report(start_date='', end_date='', department='ALL'):
    staff_query = Staff.query
    if department != 'ALL':
        staff_query = staff_query.filter_by(department=department)
    report_data = []
    for staff in staff_query.order_by(Staff.name).all():
        attendance_query = Attendance.query.filter_by(staff_id=staff.id)
        if start_date and end_date:
            attendance_query = attendance_query.filter(
                Attendance.date >= start_date,
                Attendance.date <= end_date
            )
        logs = attendance_query.order_by(Attendance.date).all()
        report_data.append({
            'staff': {'id': staff.id, 'name': staff.name, 'department': staff.department},
            'days_worked': sum(1 for log in logs if log.status != 'ABSENT'),
            'off_day_work': sum(1 for log in logs if log.is_off_day_work),
            'holiday_work': sum(1 for log in logs if log.is_holiday_work),
            'total_hours': sum(
                log.hours_worked for log in logs if log.status != 'ABSENT'
            ),
            'overtime_hours': sum(
                max(0.0, log.hours_worked - 8)
                for log in logs if log.status != 'ABSENT'
            ),
            'undertime_hours': sum(
                max(0.0, 8 - log.hours_worked)
                for log in logs
                if log.status == 'PRESENT' and log.hours_worked < 8
            )
        })
    return report_data

def validated_report_dates(start_date, end_date):
    if not start_date and not end_date:
        return True
    if not is_valid_iso_date(start_date) or not is_valid_iso_date(end_date):
        return False
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    return end >= start

def attendance_rows(report_data):
    for row in report_data:
        staff = row['staff']
        yield (
            staff['id'], staff['name'], staff['department'],
            row['days_worked'], row['off_day_work'], row['holiday_work'],
            f"{row['total_hours']:.2f}", f"{row['overtime_hours']:.2f}",
            f"{row['undertime_hours']:.2f}"
        )

def csv_download(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(
        [spreadsheet_safe_cell(value) for value in row] for row in rows
    )
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

def excel_download(filename, headers, rows):
    head = ''.join(f'<th>{escape(str(value))}</th>' for value in headers)
    body = ''.join(
        '<tr>' + ''.join(
            f'<td>{escape(spreadsheet_safe_cell(value))}</td>'
            for value in row
        ) + '</tr>'
        for row in rows
    )
    content = (
        '<html><head><meta charset="utf-8"></head><body><table>'
        f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
        '</table></body></html>'
    )
    return Response(
        content,
        mimetype='application/vnd.ms-excel',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

@app.route('/hr/export/csv')
@app.route('/hr/export_csv')
def export_hr_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    department = request.args.get('department', 'ALL')
    if not validated_report_dates(start_date, end_date):
        return "Invalid date range.", 400
    data = get_staff_attendance_report(start_date, end_date, department)
    return csv_download(
        'hr_attendance_report.csv',
        ['Staff ID', 'Employee Name', 'Department', 'Days Worked',
         'Off Days', 'Holidays', 'Total Hours', 'Overtime Hours',
         'Undertime Hours'],
        attendance_rows(data)
    )

@app.route('/hr/export/excel')
@app.route('/hr/export_excel')
def export_hr_excel():
    if 'user' not in session:
        return redirect(url_for('login'))
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    department = request.args.get('department', 'ALL')
    if not validated_report_dates(start_date, end_date):
        return "Invalid date range.", 400
    data = get_staff_attendance_report(start_date, end_date, department)
    return excel_download(
        'hr_attendance_report.xls',
        ['Staff ID', 'Employee Name', 'Department', 'Days Worked',
         'Off Days', 'Holidays', 'Total Hours', 'Overtime Hours',
         'Undertime Hours'],
        attendance_rows(data)
    )

@app.route('/hr/staff/<staff_id>')
def individual_staff_report(staff_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    staff = Staff.query.get(staff_id)
    if not staff:
        return "Staff member not found", 404
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    if not validated_report_dates(start_date, end_date):
        return "Invalid date range.", 400
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    query = Attendance.query.filter_by(staff_id=staff.id)
    if start_date and end_date:
        query = query.filter(
            Attendance.date >= start_date,
            Attendance.date <= end_date
        )
    history = query.order_by(Attendance.date.desc()).all()
    worked = [entry for entry in history if entry.status != 'ABSENT']
    return render_template(
        'staff_ledger.html',
        staff=staff,
        history=history,
        start_date=start_date,
        end_date=end_date,
        summary={
            'total_days': len(worked),
            'total_hours': sum(entry.hours_worked for entry in worked),
            'off_day_shifts': sum(1 for entry in worked if entry.is_off_day_work),
            'holiday_shifts': sum(1 for entry in worked if entry.is_holiday_work)
        }
    )

def staff_ledger_export(staff_id, spreadsheet=False):
    if 'user' not in session:
        return redirect(url_for('login'))
    staff = Staff.query.get(staff_id)
    if not staff:
        return "Staff member not found", 404
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    if not validated_report_dates(start_date, end_date):
        return "Invalid date range.", 400
    query = Attendance.query.filter_by(staff_id=staff.id)
    if start_date and end_date:
        query = query.filter(
            Attendance.date >= start_date,
            Attendance.date <= end_date
        )
    logs = query.order_by(Attendance.date).all()
    headers = [
        'Staff ID', 'Employee Name', 'Department', 'Date', 'Status',
        'Hours Worked', 'Off Day Work', 'Holiday Work', 'Notes'
    ]
    rows = (
        (
            staff.id, staff.name, staff.department, log.date, log.status,
            f'{log.hours_worked:.2f}', log.is_off_day_work,
            log.is_holiday_work, log.notes or ''
        )
        for log in logs
    )
    if not spreadsheet:
        return csv_download(f'{staff.id}_attendance.csv', headers, rows)

    return excel_download(f'{staff.id}_attendance.xls', headers, rows)

@app.route('/hr/staff/<staff_id>/export/csv')
def export_staff_ledger_csv(staff_id):
    return staff_ledger_export(staff_id)

@app.route('/hr/staff/<staff_id>/export/xls')
def export_staff_ledger_xls(staff_id):
    return staff_ledger_export(staff_id, spreadsheet=True)

# --- CSV & EXPORT ROUTES ---
@app.route('/export_csv')
def export_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Date', 'Customer Name', 'Category', 'Pax', 'Amount', 'Status', 'Payment Method', 'Reference'])
    for t in Transaction.query.all():
        writer.writerow([
            spreadsheet_safe_cell(value)
            for value in (
                t.id, t.date, t.customer_name, t.category, t.pax, t.amount,
                t.status, t.payment_method, t.reference
            )
        ])
    output.seek(0)
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=thika_gymkhana_transactions.csv"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)