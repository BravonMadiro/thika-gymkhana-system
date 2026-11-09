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

ADJUSTMENT_REASON_CODES = {
    'COUNT_CORRECTION': 'Count correction',
    'DAMAGE': 'Damage',
    'THEFT': 'Theft / loss',
    'EXPIRY': 'Expiry',
    'OTHER': 'Other'
}

def item_unit_cost(item):
    """Return the item's current weighted-average cost as a finite Decimal."""
    if not item or item.unit_cost is None:
        return None
    try:
        cost = Decimal(str(item.unit_cost))
    except (InvalidOperation, ValueError):
        return None
    return cost if cost.is_finite() else None

def movement_value(quantity, unit_cost):
    if unit_cost is None:
        return None
    return (
        Decimal(quantity) * Decimal(str(unit_cost))
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def inventory_item_value(item):
    return movement_value(item.current_stock, item_unit_cost(item))

def record_stock_movement(
    item, movement_type, quantity, reference, unit_cost=None,
    balance_after=None, department_id=None, reason_code=None,
    expense_id=None, goods_receipt_id=None
):
    """Write one valued stock-ledger line with a cost snapshot and balance."""
    movement = StockMovement(
        item_id=item.id,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        total_value=movement_value(quantity, unit_cost),
        balance_after=balance_after,
        reference=reference,
        department_id=department_id,
        reason_code=reason_code,
        expense_id=expense_id,
        goods_receipt_id=goods_receipt_id,
        movement_date=datetime.utcnow(),
        created_by=session.get('user')
    )
    db.session.add(movement)
    return movement

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
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    receipt_status = db.Column(db.String(30), nullable=False, default='NOT_REQUIRED')
    approved_by = db.Column(db.String(64), nullable=True)
    approved_at = db.Column(db.String(30), nullable=True)
    rejected_by = db.Column(db.String(64), nullable=True)
    rejection_reason = db.Column(db.String(500), nullable=True)

    supplier = db.relationship('Supplier')
    department = db.relationship('Department')
    lines = db.relationship(
        'ExpenseLine', back_populates='expense',
        cascade='all, delete-orphan', order_by='ExpenseLine.line_no'
    )
    receipts = db.relationship(
        'GoodsReceipt', back_populates='expense',
        cascade='all, delete-orphan', order_by='GoodsReceipt.received_at'
    )

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

class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    contact_person = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExpenseLine(db.Model):
    __tablename__ = 'expense_lines'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    expense_id = db.Column(db.String(32), db.ForeignKey('expenses.id'), nullable=False)
    line_no = db.Column(db.Integer, nullable=False, default=1)
    inventory_item_id = db.Column(
        db.Integer, db.ForeignKey('inventory_items.id'), nullable=True
    )
    description = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(60), nullable=False, default='Services & Other')
    quantity_ordered = db.Column(db.Integer, nullable=False, default=1)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    quantity_received = db.Column(db.Integer, nullable=False, default=0)
    quantity_rejected = db.Column(db.Integer, nullable=False, default=0)

    expense = db.relationship('Expense', back_populates='lines')
    inventory_item = db.relationship('InventoryItem')

class GoodsReceipt(db.Model):
    __tablename__ = 'goods_receipts'
    id = db.Column(db.String(32), primary_key=True)
    expense_id = db.Column(db.String(32), db.ForeignKey('expenses.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    received_by = db.Column(db.String(64), nullable=True)
    delivery_note_ref = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    expense = db.relationship('Expense', back_populates='receipts')
    supplier = db.relationship('Supplier')
    department = db.relationship('Department')
    lines = db.relationship(
        'GoodsReceiptLine', backref='receipt', cascade='all, delete-orphan'
    )

class GoodsReceiptLine(db.Model):
    __tablename__ = 'goods_receipt_lines'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    goods_receipt_id = db.Column(
        db.String(32), db.ForeignKey('goods_receipts.id'), nullable=False
    )
    expense_line_id = db.Column(
        db.Integer, db.ForeignKey('expense_lines.id'), nullable=False
    )
    inventory_item_id = db.Column(
        db.Integer, db.ForeignKey('inventory_items.id'), nullable=True
    )
    quantity_received = db.Column(db.Integer, nullable=False, default=0)
    quantity_rejected = db.Column(db.Integer, nullable=False, default=0)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    price_variance = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    variance_reason = db.Column(db.String(500), nullable=True)

    expense_line = db.relationship('ExpenseLine')
    inventory_item = db.relationship('InventoryItem')

class AssetTransfer(db.Model):
    __tablename__ = 'asset_transfers'
    id = db.Column(db.String(32), primary_key=True)
    date_sent = db.Column(db.String(30), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    requested_by = db.Column(db.String(100), nullable=False)
    approved_by = db.Column(db.String(64), nullable=True)
    custodian = db.Column(db.String(100), nullable=False)
    return_date = db.Column(db.String(30), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='Active')
    discrepancy_notes = db.Column(db.Text, nullable=True, default='N/A')
    recovery_bill_id = db.Column(db.String(32), nullable=True)
    reconciled_by = db.Column(db.String(64), nullable=True)

    department = db.relationship('Department')

class TransferItem(db.Model):
    __tablename__ = 'transfer_items'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    transfer_id = db.Column(db.String(32), db.ForeignKey('asset_transfers.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=True)
    asset_tracking_id = db.Column(db.Integer, db.ForeignKey('asset_tracking.id'), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    sent = db.Column(db.Integer, nullable=False, default=1)
    returned_good = db.Column(db.Integer, nullable=False, default=0)
    damaged = db.Column(db.Integer, nullable=False, default=0)
    missing = db.Column(db.Integer, nullable=False, default=0)
    unit_cost_at_issue = db.Column(db.Numeric(10, 2), nullable=True)

    inventory_item = db.relationship('InventoryItem')
    asset_tracking = db.relationship('AssetTracking')

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
    issued_at = db.Column(db.DateTime, nullable=True)
    unit_cost_at_issue = db.Column(db.Numeric(10, 2), nullable=True)
    issued_value = db.Column(db.Numeric(16, 2), nullable=True)
    rejected_by = db.Column(db.String(64), nullable=True)
    rejection_reason = db.Column(db.String(500), nullable=True)

    item = db.relationship('InventoryItem')
    department = db.relationship('Department')

class AssetTracking(db.Model):
    __tablename__ = 'asset_tracking'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=False)
    transfer_id = db.Column(db.String(32), db.ForeignKey('asset_transfers.id'), nullable=True)
    borrower_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    borrowed_at = db.Column(db.DateTime, default=datetime.utcnow)
    returned_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='OUT')
    inspection_notes = db.Column(db.Text, nullable=True)
    recovery_triggered = db.Column(db.Boolean, default=False)
    issued_by = db.Column(db.String(64), nullable=True)
    returned_by = db.Column(db.String(64), nullable=True)
    unit_cost_at_issue = db.Column(db.Numeric(10, 2), nullable=True)
    recovery_expense_id = db.Column(db.String(32), nullable=True, unique=True)

    item = db.relationship('InventoryItem')
    department = db.relationship('Department')
    transfer = db.relationship('AssetTransfer')

class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('inventory_items.id'), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=True)
    total_value = db.Column(db.Numeric(16, 2), nullable=True)
    balance_after = db.Column(db.Integer, nullable=True)
    reference = db.Column(db.String(100), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    reason_code = db.Column(db.String(30), nullable=True)
    expense_id = db.Column(db.String(32), db.ForeignKey('expenses.id'), nullable=True)
    goods_receipt_id = db.Column(
        db.String(32), db.ForeignKey('goods_receipts.id'), nullable=True
    )
    movement_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(64), nullable=True)

    item = db.relationship('InventoryItem', backref='movements')
    department = db.relationship('Department')
    expense = db.relationship('Expense')
    goods_receipt = db.relationship('GoodsReceipt')

class FixedAsset(db.Model):
    __tablename__ = 'fixed_assets'
    id = db.Column(db.Integer, primary_key=True)
    asset_tag = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(60), nullable=False, default='Other')
    subcategory = db.Column(db.String(60), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    serial_number = db.Column(db.String(120), nullable=True)
    inventory_item_id = db.Column(
        db.Integer, db.ForeignKey('inventory_items.id'), nullable=True
    )
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    custodian = db.Column(db.String(120), nullable=True)
    acquisition_date = db.Column(db.Date, nullable=False)
    acquisition_cost = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    salvage_value = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    useful_life_months = db.Column(db.Integer, nullable=False, default=60)
    depreciation_method = db.Column(
        db.String(30), nullable=False, default='STRAIGHT_LINE'
    )
    accumulated_depreciation = db.Column(
        db.Numeric(16, 2), nullable=False, default=0
    )
    last_depreciated_period = db.Column(db.String(7), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='IN_USE')
    disposal_date = db.Column(db.Date, nullable=True)
    disposal_proceeds = db.Column(db.Numeric(16, 2), nullable=True)
    disposal_reason = db.Column(db.String(500), nullable=True)
    gain_loss_on_disposal = db.Column(db.Numeric(16, 2), nullable=True)
    asset_tracking_id = db.Column(
        db.Integer, db.ForeignKey('asset_tracking.id'), nullable=True
    )
    transfer_item_id = db.Column(
        db.Integer, db.ForeignKey('transfer_items.id'), nullable=True
    )
    is_capitalized = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    capitalized_at = db.Column(db.DateTime, default=datetime.utcnow)
    decapitalized_at = db.Column(db.DateTime, nullable=True)
    recovery_basis = db.Column(db.Numeric(16, 2), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(64), nullable=True)

    inventory_item = db.relationship('InventoryItem')
    supplier = db.relationship('Supplier')
    department = db.relationship('Department')
    asset_tracking = db.relationship('AssetTracking')
    transfer_item = db.relationship('TransferItem')
    depreciation_entries = db.relationship(
        'DepreciationEntry', back_populates='fixed_asset',
        cascade='all, delete-orphan', order_by='DepreciationEntry.period'
    )

    @property
    def depreciable_base(self):
        cost = Decimal(str(self.acquisition_cost or 0))
        salvage = Decimal(str(self.salvage_value or 0))
        return max(cost - salvage, Decimal('0.00'))

    @property
    def monthly_depreciation(self):
        if not self.useful_life_months or self.useful_life_months <= 0:
            return Decimal('0.00')
        return (
            self.depreciable_base / Decimal(self.useful_life_months)
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def net_book_value(self):
        if not self.is_capitalized or self.status in {'DISPOSED', 'LOST'}:
            return Decimal('0.00')
        cost = Decimal(str(self.acquisition_cost or 0))
        accumulated = Decimal(str(self.accumulated_depreciation or 0))
        salvage = Decimal(str(self.salvage_value or 0))
        return max(cost - accumulated, salvage, Decimal('0.00'))

class DepreciationEntry(db.Model):
    __tablename__ = 'depreciation_entries'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fixed_asset_id = db.Column(
        db.Integer, db.ForeignKey('fixed_assets.id'), nullable=False
    )
    period = db.Column(db.String(7), nullable=False)
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    accumulated_after = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(64), nullable=True)

    fixed_asset = db.relationship('FixedAsset', back_populates='depreciation_entries')

    __table_args__ = (
        db.UniqueConstraint(
            'fixed_asset_id', 'period',
            name='uq_depreciation_asset_period'
        ),
    )

class RecoveryReceivable(db.Model):
    __tablename__ = 'recovery_receivables'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    reference = db.Column(db.String(32), unique=True, nullable=False)
    asset_tracking_id = db.Column(
        db.Integer, db.ForeignKey('asset_tracking.id'), nullable=True
    )
    transfer_id = db.Column(
        db.String(32), db.ForeignKey('asset_transfers.id'), nullable=True
    )
    transfer_item_id = db.Column(
        db.Integer, db.ForeignKey('transfer_items.id'), nullable=True
    )
    fixed_asset_id = db.Column(
        db.Integer, db.ForeignKey('fixed_assets.id'), nullable=True
    )
    legacy_expense_id = db.Column(
        db.String(32), db.ForeignKey('expenses.id'), nullable=True, unique=True
    )
    borrower_name = db.Column(db.String(120), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)
    description = db.Column(db.String(500), nullable=False)
    basis = db.Column(db.String(30), nullable=False, default='ISSUE_COST')
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    amount_settled = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    issue_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='OPEN')
    notes = db.Column(db.String(500), nullable=True)
    written_off_at = db.Column(db.DateTime, nullable=True)
    written_off_by = db.Column(db.String(64), nullable=True)
    write_off_reason = db.Column(db.String(500), nullable=True)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset_tracking = db.relationship('AssetTracking')
    transfer = db.relationship('AssetTransfer')
    transfer_item = db.relationship('TransferItem')
    fixed_asset = db.relationship('FixedAsset')
    legacy_expense = db.relationship('Expense')
    department = db.relationship('Department')
    settlements = db.relationship(
        'RecoverySettlement', back_populates='receivable',
        cascade='all, delete-orphan', order_by='RecoverySettlement.settled_on'
    )

    @property
    def balance(self):
        amount = Decimal(str(self.amount or 0))
        settled = Decimal(str(self.amount_settled or 0))
        return max(amount - settled, Decimal('0.00'))

    @property
    def is_overdue(self):
        return bool(
            self.status in {'OPEN', 'PARTIALLY_SETTLED'}
            and self.due_date and self.due_date < datetime.now().date()
        )

    __table_args__ = (
        db.UniqueConstraint(
            'asset_tracking_id', name='uq_recovery_asset_tracking'
        ),
        db.UniqueConstraint('transfer_id', name='uq_recovery_transfer'),
    )

class RecoverySettlement(db.Model):
    __tablename__ = 'recovery_settlements'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    receivable_id = db.Column(
        db.Integer, db.ForeignKey('recovery_receivables.id'), nullable=False
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    settled_on = db.Column(db.Date, nullable=False)
    method = db.Column(db.String(30), nullable=False, default='CASH')
    reference = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    recorded_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    receivable = db.relationship(
        'RecoveryReceivable', back_populates='settlements'
    )

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

PROCUREMENT_CATEGORIES = tuple(INVENTORY_CATEGORIES.keys()) + ('Services & Other',)
FIXED_ASSET_CATEGORIES = tuple(INVENTORY_CATEGORIES.keys()) + ('Other',)
FIXED_ASSET_DEFAULT_LIVES = {
    'Housekeeping': 60,
    'Food & Beverage': 60,
    'Maintenance & Engineering': 60,
    'Events & Banqueting': 60,
    'Sports & Recreation': 60,
    'Office & Administration': 36,
    'Safety & Security': 36,
    'Furniture & Appliances': 120,
    'Other': 60
}
DEPRECIABLE_STATUSES = {'IN_USE', 'IN_STORE', 'UNDER_REPAIR'}
FIXED_ASSET_STATUSES = {
    'IN_USE', 'IN_STORE', 'UNDER_REPAIR', 'DISPOSED', 'LOST'
}
try:
    FIXED_ASSET_CAPITALIZATION_THRESHOLD = Decimal(
        os.environ.get('FIXED_ASSET_CAPITALIZATION_THRESHOLD', '0')
    )
except InvalidOperation:
    FIXED_ASSET_CAPITALIZATION_THRESHOLD = Decimal('0')
if not FIXED_ASSET_CAPITALIZATION_THRESHOLD.is_finite():
    FIXED_ASSET_CAPITALIZATION_THRESHOLD = Decimal('0')
RECOVERY_STATUSES = {'OPEN', 'PARTIALLY_SETTLED', 'SETTLED', 'WRITTEN_OFF'}
RECOVERY_BASIS = {'NBV', 'ISSUE_COST', 'REPLACEMENT', 'AGREED'}
RECOVERY_SETTLEMENT_METHODS = {
    'CASH', 'MPESA', 'BANK', 'PAYROLL', 'OTHER'
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
                'issued_at': 'DATETIME',
                'unit_cost_at_issue': 'NUMERIC(10, 2)',
                'issued_value': 'NUMERIC(16, 2)',
                'rejected_by': 'VARCHAR(64)',
                'rejection_reason': 'VARCHAR(500)'
            },
            'asset_tracking': {
                'issued_by': 'VARCHAR(64)',
                'returned_by': 'VARCHAR(64)',
                'transfer_id': 'VARCHAR(32)',
                'unit_cost_at_issue': 'NUMERIC(10, 2)',
                'recovery_expense_id': 'VARCHAR(32)'
            },
            'asset_transfers': {
                'department_id': 'INTEGER',
                'reconciled_by': 'VARCHAR(64)'
            },
            'transfer_items': {
                'item_id': 'INTEGER',
                'asset_tracking_id': 'INTEGER',
                'unit_cost_at_issue': 'NUMERIC(10, 2)'
            },
            'expenses': {
                'linked_asset_tracking_id': 'INTEGER',
                'supplier_id': 'INTEGER',
                'department_id': 'INTEGER',
                'receipt_status': "VARCHAR(30) NOT NULL DEFAULT 'NOT_REQUIRED'",
                'approved_by': 'VARCHAR(64)',
                'approved_at': 'VARCHAR(30)',
                'rejected_by': 'VARCHAR(64)',
                'rejection_reason': 'VARCHAR(500)'
            },
            'fixed_assets': {
                'asset_tracking_id': 'INTEGER',
                'transfer_item_id': 'INTEGER',
                'is_capitalized': 'BOOLEAN NOT NULL DEFAULT TRUE',
                'capitalized_at': 'DATETIME',
                'decapitalized_at': 'DATETIME',
                'recovery_basis': 'NUMERIC(16, 2)'
            },
            'stock_movements': {
                'created_by': 'VARCHAR(64)',
                'unit_cost': 'NUMERIC(10, 2)',
                'total_value': 'NUMERIC(16, 2)',
                'balance_after': 'INTEGER',
                'movement_date': 'DATETIME',
                'reason_code': 'VARCHAR(30)',
                'expense_id': 'VARCHAR(32)',
                'goods_receipt_id': 'VARCHAR(32)'
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
        # Phase 1 backfill: give legacy records a usable cost/value snapshot and
        # move date. Historical balances are left null because they cannot be
        # reconstructed reliably from the old quantity-only ledger.
        try:
            for movement in StockMovement.query.filter(
                StockMovement.unit_cost.is_(None)
            ).all():
                if movement.movement_date is None:
                    movement.movement_date = movement.created_at or datetime.utcnow()
                cost = item_unit_cost(movement.item)
                if cost is not None:
                    movement.unit_cost = cost
                    movement.total_value = movement_value(movement.quantity, cost)
            for requisition in StoreRequisition.query.filter_by(status='ISSUED').all():
                if requisition.issued_at is None:
                    requisition.issued_at = requisition.created_at or datetime.utcnow()
                if requisition.unit_cost_at_issue is None:
                    cost = item_unit_cost(requisition.item)
                    if cost is not None:
                        requisition.unit_cost_at_issue = cost
                        requisition.issued_value = movement_value(
                            requisition.quantity, cost
                        )
            for tracking in AssetTracking.query.all():
                if tracking.unit_cost_at_issue is None:
                    tracking.unit_cost_at_issue = item_unit_cost(tracking.item)
            for line in TransferItem.query.all():
                if line.unit_cost_at_issue is None:
                    line.unit_cost_at_issue = item_unit_cost(line.inventory_item)
            for transfer in AssetTransfer.query.filter(
                AssetTransfer.department_id.is_(None)
            ).all():
                destination = (transfer.destination or '').strip().lower()
                if not destination:
                    continue
                department = Department.query.filter(
                    db.func.lower(Department.name) == destination
                ).first()
                if department:
                    transfer.department_id = department.id
            # Legacy LPOs had a free-text description and a single amount. Give
            # them one line so receipts, matching, and category reporting work.
            for expense in Expense.query.filter(
                Expense.doc_type.in_({'LPO', 'Bill'})
            ).all():
                if ExpenseLine.query.filter_by(expense_id=expense.id).first():
                    continue
                try:
                    amount = Decimal(expense.estimated_amount)
                except (InvalidOperation, TypeError):
                    amount = Decimal('0.00')
                if not amount.is_finite() or amount < 0:
                    amount = Decimal('0.00')
                description = (expense.item_description or 'Legacy procurement line')[:500]
                db.session.add(ExpenseLine(
                    expense_id=expense.id,
                    line_no=1,
                    description=description,
                    category='General',
                    quantity_ordered=1,
                    unit_cost=amount,
                    line_total=amount
                ))
                expense.receipt_status = 'NOT_REQUIRED'
            # Legacy recoveries were stored as Recovery expenses. Move them into
            # the receivable register while retaining the original expense link.
            legacy_sequence = 1
            existing_references = {
                receivable.reference
                for receivable in RecoveryReceivable.query.all()
            }
            for expense in Expense.query.filter_by(doc_type='Recovery').all():
                if RecoveryReceivable.query.filter_by(
                    legacy_expense_id=expense.id
                ).first():
                    continue
                while f"RCV{legacy_sequence:05d}" in existing_references:
                    legacy_sequence += 1
                reference = f"RCV{legacy_sequence:05d}"
                existing_references.add(reference)
                try:
                    amount = Decimal(expense.estimated_amount)
                except (InvalidOperation, TypeError):
                    amount = Decimal('0.00')
                if not amount.is_finite() or amount < 0:
                    amount = Decimal('0.00')
                try:
                    settled = Decimal(expense.actual_amount)
                except (InvalidOperation, TypeError):
                    settled = Decimal('0.00')
                if not settled.is_finite() or settled < 0:
                    settled = Decimal('0.00')
                settled = min(settled, amount)
                issue_date = datetime.now().date()
                try:
                    issue_date = datetime.strptime(
                        (expense.date_created or '')[:10], '%Y-%m-%d'
                    ).date()
                except ValueError:
                    pass
                borrower = expense.caretaker_name or 'Unknown'
                department_id = expense.department_id
                if expense.linked_asset_tracking_id:
                    tracking = db.session.get(
                        AssetTracking, expense.linked_asset_tracking_id
                    )
                    if tracking:
                        borrower = tracking.borrower_name or borrower
                        department_id = tracking.department_id or department_id
                elif expense.linked_transfer_id:
                    transfer = db.session.get(
                        AssetTransfer, expense.linked_transfer_id
                    )
                    if transfer:
                        borrower = transfer.custodian or borrower
                        department_id = transfer.department_id or department_id
                if amount > 0 and settled >= amount:
                    legacy_status = 'SETTLED'
                elif settled > 0:
                    legacy_status = 'PARTIALLY_SETTLED'
                else:
                    legacy_status = 'OPEN'
                receivable = RecoveryReceivable(
                    reference=reference,
                    asset_tracking_id=expense.linked_asset_tracking_id,
                    transfer_id=expense.linked_transfer_id,
                    legacy_expense_id=expense.id,
                    borrower_name=borrower,
                    department_id=department_id,
                    description=(
                        expense.item_description or 'Legacy recovery'
                    )[:500],
                    basis='ISSUE_COST',
                    amount=amount,
                    amount_settled=settled,
                    issue_date=issue_date,
                    due_date=issue_date + timedelta(days=30),
                    status=legacy_status,
                    created_by=expense.logged_by
                )
                db.session.add(receivable)
                db.session.flush()
                if settled > 0:
                    db.session.add(RecoverySettlement(
                        receivable_id=receivable.id,
                        amount=settled,
                        settled_on=issue_date,
                        method='OTHER',
                        reference=expense.invoice_ref,
                        notes='Legacy recovery settlement migrated',
                        recorded_by=expense.fulfilled_by or expense.logged_by
                    ))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Failed to backfill inventory accounting fields")
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
    elif doc_type_upper in {'GRN', 'GOODS_RECEIPT'}:
        prefix = 'GRN'
    elif doc_type_upper in {'RECOVERY_RECEIVABLE', 'RECOVERY', 'RCV'}:
        prefix = 'RCV'
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
        elif prefix == 'GRN':
            exists = GoodsReceipt.query.get(full_code) is not None
        elif prefix == 'RCV':
            exists = RecoveryReceivable.query.filter_by(
                reference=full_code
            ).first() is not None
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

def is_valid_period(value):
    if not re.fullmatch(r'\d{4}-\d{2}', value or ''):
        return False
    year, month = (int(part) for part in value.split('-'))
    return 1 <= month <= 12 and year >= 1900

def parse_period(value):
    if not is_valid_period(value):
        return None
    year, month = (int(part) for part in value.split('-'))
    return year, month

def period_string(year, month):
    return f"{year:04d}-{month:02d}"

def add_period_months(year, month, delta):
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1

def next_period(period):
    year, month = parse_period(period)
    if year is None:
        return None
    year, month = add_period_months(year, month, 1)
    return period_string(year, month)

def period_end_date(period):
    year, month = parse_period(period)
    if year is None:
        return None
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).date()

def fixed_asset_default_life(category):
    return FIXED_ASSET_DEFAULT_LIVES.get(category, 60)

def generate_fixed_asset_tag():
    last = FixedAsset.query.order_by(FixedAsset.id.desc()).first()
    sequence = (last.id + 1) if last else 1
    while True:
        tag = f"FA{sequence:05d}"
        if not FixedAsset.query.filter_by(asset_tag=tag).first():
            return tag
        sequence += 1

def capitalize_tracking_asset(tracking, unit_cost=None, transfer_item_id=None):
    """Capitalise a placed-in-service durable asset from its issue snapshot."""
    if not tracking or not tracking.item:
        return None
    if FixedAsset.query.filter_by(asset_tracking_id=tracking.id).first():
        return None
    item = tracking.item
    cost = (
        Decimal(str(unit_cost)) if unit_cost is not None
        else (
            Decimal(str(tracking.unit_cost_at_issue))
            if tracking.unit_cost_at_issue is not None
            else item_unit_cost(item)
        )
    )
    if cost is None or not cost.is_finite() or cost <= 0:
        return None
    total_cost = (cost * tracking.quantity).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    if (
        total_cost <= 0
        or total_cost < FIXED_ASSET_CAPITALIZATION_THRESHOLD
    ):
        return None
    acquisition_date = (
        tracking.borrowed_at.date()
        if tracking.borrowed_at else datetime.now().date()
    )
    asset = FixedAsset(
        asset_tag=generate_fixed_asset_tag(),
        name=item.name,
        category=(
            item.category if item.category in FIXED_ASSET_CATEGORIES else 'Other'
        ),
        subcategory=item.subcategory,
        inventory_item_id=item.id,
        department_id=tracking.department_id,
        custodian=tracking.borrower_name,
        acquisition_date=acquisition_date,
        acquisition_cost=total_cost,
        quantity=tracking.quantity,
        salvage_value=Decimal('0.00'),
        useful_life_months=fixed_asset_default_life(
            item.category if item.category in FIXED_ASSET_CATEGORIES else 'Other'
        ),
        depreciation_method='STRAIGHT_LINE',
        accumulated_depreciation=Decimal('0.00'),
        status='IN_USE',
        is_capitalized=True,
        capitalized_at=datetime.utcnow(),
        asset_tracking_id=tracking.id,
        transfer_item_id=transfer_item_id,
        created_by=session.get('user')
    )
    db.session.add(asset)
    return asset


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
    return Expense.query.filter(
        Expense.doc_type != 'Recovery'
    ).order_by(Expense.date_created.desc()).all()

def get_transfers():
    return AssetTransfer.query.order_by(AssetTransfer.date_sent.desc()).all()

def get_transfer_items(transfer):
    items = TransferItem.query.filter_by(transfer_id=transfer.id).all()
    if items:
        return [{'name': i.name, 'sent': i.sent, 'returned_good': i.returned_good, 'damaged': i.damaged, 'missing': i.missing} for i in items]
    return []

@app.context_processor
def inject_initial_identity():
    authenticated = 'user' in session
    return {
        'initial_identity': {
            'authenticated': authenticated,
            'user': session.get('user') if authenticated else None,
            'display_name': (
                session.get('display_name') or session.get('user')
                if authenticated else None
            ),
            'role': session.get('role') if authenticated else None,
            'bootstrap_required': (
                False if authenticated else User.query.count() == 0
            )
        }
    }

def expense_requires_receipt(expense):
    return any(line.inventory_item_id for line in expense.lines)

def expense_line_outstanding(line):
    return line.quantity_ordered - line.quantity_received - line.quantity_rejected

def expense_received_value(expense):
    total = Decimal('0.00')
    for receipt in expense.receipts:
        for line in receipt.lines:
            total += Decimal(str(line.unit_cost)) * line.quantity_received
    return total

def expense_ordered_value(expense):
    return sum(
        (Decimal(str(line.line_total)) for line in expense.lines),
        Decimal('0.00')
    )

def expense_price_variance(expense):
    return sum(
        (
            Decimal(str(line.price_variance))
            for receipt in expense.receipts
            for line in receipt.lines
        ),
        Decimal('0.00')
    )

def expense_receipt_status(expense):
    inventory_lines = [
        line for line in expense.lines if line.inventory_item_id
    ]
    if not inventory_lines:
        return 'NOT_REQUIRED'
    if all(expense_line_outstanding(line) <= 0 for line in inventory_lines):
        return 'RECEIVED'
    if any(
        line.quantity_received + line.quantity_rejected > 0
        for line in inventory_lines
    ):
        return 'PARTIALLY_RECEIVED'
    return 'PENDING'

def expense_match_status(expense):
    status = expense.receipt_status or expense_receipt_status(expense)
    if status in {'PENDING', 'PARTIALLY_RECEIVED'}:
        return 'AWAITING_RECEIPT'
    if status == 'NOT_REQUIRED':
        paid = Decimal(str(expense.actual_amount or '0'))
        estimated = Decimal(str(expense.estimated_amount or '0'))
        return 'MATCHED' if paid <= estimated else 'VARIANCE'
    received = expense_received_value(expense)
    paid = Decimal(str(expense.actual_amount or '0'))
    variance = expense_price_variance(expense)
    if variance != Decimal('0.00') or paid > received:
        return 'VARIANCE'
    return 'MATCHED' if paid >= received else 'AWAITING_INVOICE'

def serialize_expense(expense, payments=None):
    lines = []
    for line in expense.lines:
        receipt_lines = [
            receipt_line
            for receipt in expense.receipts
            for receipt_line in receipt.lines
            if receipt_line.expense_line_id == line.id
        ]
        received_value = sum(
            (
                Decimal(str(receipt_line.unit_cost))
                * receipt_line.quantity_received
                for receipt_line in receipt_lines
            ),
            Decimal('0.00')
        )
        price_variance = sum(
            (
                Decimal(str(receipt_line.price_variance))
                for receipt_line in receipt_lines
            ),
            Decimal('0.00')
        )
        outstanding = max(expense_line_outstanding(line), 0)
        lines.append({
            'id': line.id,
            'line_no': line.line_no,
            'inventory_item_id': line.inventory_item_id,
            'item': line.inventory_item.name if line.inventory_item else None,
            'description': line.description,
            'category': line.category,
            'quantity_ordered': line.quantity_ordered,
            'quantity_received': line.quantity_received,
            'quantity_rejected': line.quantity_rejected,
            'outstanding': outstanding,
            'unit_cost': str(line.unit_cost),
            'line_total': str(line.line_total),
            'received_value': str(received_value),
            'price_variance': str(price_variance)
        })
    receipts = []
    for receipt in expense.receipts:
        receipts.append({
            'id': receipt.id,
            'received_at': receipt.received_at.isoformat() if receipt.received_at else None,
            'received_by': receipt.received_by,
            'delivery_note_ref': receipt.delivery_note_ref,
            'notes': receipt.notes,
            'lines': [{
                'expense_line_id': receipt_line.expense_line_id,
                'item': (
                    receipt_line.expense_line.description
                    if receipt_line.expense_line else ''
                ),
                'quantity_received': receipt_line.quantity_received,
                'quantity_rejected': receipt_line.quantity_rejected,
                'unit_cost': str(receipt_line.unit_cost),
                'price_variance': str(receipt_line.price_variance),
                'variance_reason': receipt_line.variance_reason
            } for receipt_line in receipt.lines]
        })
    received_value = expense_received_value(expense)
    ordered_value = expense_ordered_value(expense)
    price_variance = expense_price_variance(expense)
    outstanding_value = sum(
        (
            Decimal(str(line.unit_cost)) * max(expense_line_outstanding(line), 0)
            for line in expense.lines
        ),
        Decimal('0.00')
    )
    requires_receipt = expense_requires_receipt(expense)
    can_receive = (
        requires_receipt
        and expense.status not in {'Pending Approval', 'Rejected'}
        and any(expense_line_outstanding(line) > 0 for line in expense.lines)
    )
    paid_value = Decimal(str(expense.actual_amount or '0'))
    payable_cap = (
        received_value if requires_receipt
        else Decimal(str(expense.estimated_amount or '0'))
    )
    can_pay = (
        expense.status in {'Pending Purchase', 'Partially Paid'}
        and (not requires_receipt or received_value > 0)
        and paid_value < payable_cap
    )
    return {
        'id': expense.id,
        'doc_type': expense.doc_type,
        'date_created': expense.date_created,
        'caretaker_name': expense.caretaker_name,
        'supplier_id': expense.supplier_id,
        'supplier': expense.supplier.name if expense.supplier else None,
        'department_id': expense.department_id,
        'department': expense.department.name if expense.department else None,
        'item_description': expense.item_description,
        'estimated_amount': expense.estimated_amount,
        'actual_amount': expense.actual_amount,
        'received_value': str(received_value),
        'ordered_value': str(ordered_value),
        'outstanding_value': str(outstanding_value),
        'price_variance': str(price_variance),
        'receipt_status': expense.receipt_status or expense_receipt_status(expense),
        'match_status': expense_match_status(expense),
        'requires_receipt': requires_receipt,
        'can_receive': can_receive,
        'can_pay': can_pay,
        'invoice_ref': expense.invoice_ref,
        'receipt_file': expense.receipt_file,
        'receipt_note': expense.receipt_note,
        'status': expense.status,
        'logged_by': expense.logged_by,
        'approved_by': expense.approved_by,
        'rejected_by': expense.rejected_by,
        'rejection_reason': expense.rejection_reason,
        'lines': lines,
        'receipts': receipts,
        'payments': payments or []
    }

def serialize_fixed_asset(asset):
    return {
        'id': asset.id,
        'asset_tag': asset.asset_tag,
        'name': asset.name,
        'category': asset.category,
        'subcategory': asset.subcategory,
        'description': asset.description,
        'serial_number': asset.serial_number,
        'inventory_item_id': asset.inventory_item_id,
        'inventory_item': (
            asset.inventory_item.name if asset.inventory_item else None
        ),
        'supplier_id': asset.supplier_id,
        'supplier': asset.supplier.name if asset.supplier else None,
        'department_id': asset.department_id,
        'department': asset.department.name if asset.department else None,
        'location': asset.location,
        'custodian': asset.custodian,
        'acquisition_date': (
            asset.acquisition_date.isoformat() if asset.acquisition_date else None
        ),
        'acquisition_cost': str(asset.acquisition_cost),
        'quantity': asset.quantity,
        'salvage_value': str(asset.salvage_value),
        'useful_life_months': asset.useful_life_months,
        'depreciation_method': asset.depreciation_method,
        'monthly_depreciation': str(asset.monthly_depreciation),
        'accumulated_depreciation': str(asset.accumulated_depreciation),
        'net_book_value': str(asset.net_book_value),
        'last_depreciated_period': asset.last_depreciated_period,
        'status': asset.status,
        'disposal_date': (
            asset.disposal_date.isoformat() if asset.disposal_date else None
        ),
        'disposal_proceeds': (
            str(asset.disposal_proceeds)
            if asset.disposal_proceeds is not None else None
        ),
        'disposal_reason': asset.disposal_reason,
        'gain_loss_on_disposal': (
            str(asset.gain_loss_on_disposal)
            if asset.gain_loss_on_disposal is not None else None
        ),
        'asset_tracking_id': asset.asset_tracking_id,
        'transfer_item_id': asset.transfer_item_id,
        'is_capitalized': asset.is_capitalized,
        'capitalized_at': (
            asset.capitalized_at.isoformat()
            if asset.capitalized_at else None
        ),
        'decapitalized_at': (
            asset.decapitalized_at.isoformat()
            if asset.decapitalized_at else None
        ),
        'recovery_basis': (
            str(asset.recovery_basis)
            if asset.recovery_basis is not None else None
        ),
        'created_at': asset.created_at.isoformat() if asset.created_at else None,
        'created_by': asset.created_by
    }

# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET' and 'user' in session:
        if db.session.get(User, session.get('user')):
            return redirect(url_for('index'))
        session.clear()
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
    services = get_services()
    categories = set(services)
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
        total_bill_count=len(report_transactions),
        fee_rates=services,
        role=session.get('role')
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
        ('recovery settlements', RecoverySettlement),
        ('recovery receivables', RecoveryReceivable),
        ('goods receipt lines', GoodsReceiptLine),
        ('stock movements', StockMovement),
        ('goods receipts', GoodsReceipt),
        ('purchase order lines', ExpenseLine),
        ('payment records', PaymentHistory),
        ('procurement documents', Expense),
        ('sales transactions', Transaction),
        ('asset transfer items', TransferItem),
        ('asset tracking records', AssetTracking),
        ('asset transfers', AssetTransfer),
        ('store requisitions', StoreRequisition),
        ('fixed asset depreciation entries', DepreciationEntry),
        ('fixed asset register', FixedAsset),
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
    flash(f"Operational data cleared successfully: {summary}. User accounts, service pricing, suppliers, and departments were retained.", "success")
    return redirect(url_for('admin_panel'))

@app.route('/admin/import_legacy_bill', methods=['POST'])
def import_legacy_bill():
    if 'user' not in session or session.get('role') != 'admin':
        flash("⚠️ Administrator access required to import legacy bills.", "error")
        return redirect(url_for('index'))

    customer_name = request.form.get('customer_name', '').strip()
    category = request.form.get('category', '').strip()
    historical_date = request.form.get('historical_date', '').strip()
    raw_amount = request.form.get('amount', '').strip()
    payment_method = request.form.get('payment_method', 'Cash').strip()
    reference = request.form.get('reference', 'LEGACY').strip().upper()

    if not customer_name or len(customer_name) > 120 or not category or not is_valid_iso_date(historical_date):
        flash("❌ Enter valid customer name, category, and a correct historical date (YYYY-MM-DD).", "error")
        return redirect(url_for('analytics'))

    try:
        amount = Decimal(raw_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        flash("❌ Enter a valid legacy bill amount.", "error")
        return redirect(url_for('analytics'))

    if not amount.is_finite() or amount <= 0 or amount > Decimal('999999999.99'):
        flash("❌ Legacy amount must be between 0.01 and 999,999,999.99.", "error")
        return redirect(url_for('analytics'))

    new_id = generate_prefixed_id('SERVICE_BILL', category)

    # Store with the historical date prefix (e.g., "2023-09-15 00:00") so analytics bucket it correctly
    legacy_tx = Transaction(
        id=new_id,
        date=f"{historical_date} 00:00",
        customer_name=f"{customer_name} (Legacy)",
        category=category,
        pax=1,
        amount=f"{amount:.2f}",
        status='Paid',  # Pre-settled historically
        payment_method=payment_method,
        reference=reference,
        created_by=session.get('user'),
        confirmed_by=session.get('user')
    )

    db.session.add(legacy_tx)
    db.session.commit()
    flash(f"✅ Legacy bill {new_id} imported successfully for {historical_date}.", "success")
    return redirect(url_for('analytics', date=historical_date))

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
        AssetTracking.recovery_triggered.is_(True),
        AssetTracking.transfer_id.is_(None)
    ).order_by(AssetTracking.returned_at.desc()).all()
    recovery_receivable_by_tracking = {
        receivable.asset_tracking_id: receivable
        for receivable in RecoveryReceivable.query.filter(
            RecoveryReceivable.asset_tracking_id.isnot(None)
        ).all()
    }

    return render_template(
        'inventory.html',
        items=items,
        departments=departments,
        movements=recent_movements,
        requisitions=requisitions,
        active_assets=active_assets,
        recovery_cases=recovery_cases,
        recovery_receivable_by_tracking=recovery_receivable_by_tracking,
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
    ).limit(25).all()
    requisitions = StoreRequisition.query.order_by(
        StoreRequisition.created_at.desc()
    ).limit(100).all()
    recovery_cases = AssetTracking.query.filter(
        AssetTracking.recovery_triggered.is_(True),
        AssetTracking.transfer_id.is_(None)
    ).order_by(AssetTracking.returned_at.desc()).all()
    recovery_receivable_by_tracking = {
        receivable.asset_tracking_id: receivable
        for receivable in RecoveryReceivable.query.filter(
            RecoveryReceivable.asset_tracking_id.isnot(None)
        ).all()
    }

    total_value = Decimal('0.00')
    consumable_value = Decimal('0.00')
    durable_value = Decimal('0.00')
    low_stock_value = Decimal('0.00')
    low_stock_count = 0
    cost_missing_count = 0
    valuation_by_category = {}
    item_views = []
    for item in items:
        cost = item_unit_cost(item)
        value = inventory_item_value(item)
        if cost is None:
            cost_missing_count += 1
        if value is not None:
            total_value += value
            if item.is_consumable:
                consumable_value += value
            else:
                durable_value += value
        if item.current_stock <= item.reorder_level:
            low_stock_count += 1
            if value is not None:
                low_stock_value += value
        bucket = valuation_by_category.setdefault(item.category, {
            'category': item.category,
            'items': 0,
            'quantity': 0,
            'value': Decimal('0.00')
        })
        bucket['items'] += 1
        bucket['quantity'] += item.current_stock
        if value is not None:
            bucket['value'] += value
        item_views.append({
            'id': item.id,
            'name': item.name,
            'category': item.category,
            'subcategory': item.subcategory,
            'unit_of_measure': item.unit_of_measure,
            'current_stock': item.current_stock,
            'reorder_level': item.reorder_level,
            'unit_cost': str(cost) if cost is not None else None,
            'stock_value': str(value) if value is not None else None,
            'is_consumable': item.is_consumable,
            'department': item.department.name if item.department else None
        })

    pending_requisition_value = Decimal('0.00')
    requisition_views = []
    for requisition in requisitions:
        cost = (
            Decimal(requisition.unit_cost_at_issue)
            if requisition.unit_cost_at_issue is not None
            else item_unit_cost(requisition.item)
        )
        value = (
            Decimal(requisition.issued_value)
            if requisition.issued_value is not None
            else movement_value(requisition.quantity, cost)
        )
        if requisition.status == 'PENDING' and value is not None:
            pending_requisition_value += value
        requisition_views.append({
            'id': requisition.id,
            'item': requisition.item.name,
            'department': requisition.department.name,
            'quantity': requisition.quantity,
            'unit_of_measure': requisition.item.unit_of_measure,
            'requester': requisition.requester_name,
            'status': requisition.status,
            'created_at': requisition.created_at.isoformat() if requisition.created_at else None,
            'issued_at': requisition.issued_at.isoformat() if requisition.issued_at else None,
            'unit_cost': str(cost) if cost is not None else None,
            'value': str(value) if value is not None else None,
            'rejection_reason': requisition.rejection_reason
        })

    movement_views = []
    for movement in movements:
        movement_date = movement.movement_date or movement.created_at
        movement_views.append({
            'id': movement.id,
            'item': movement.item.name,
            'movement_type': movement.movement_type,
            'quantity': movement.quantity,
            'unit_cost': str(movement.unit_cost) if movement.unit_cost is not None else None,
            'total_value': str(movement.total_value) if movement.total_value is not None else None,
            'balance_after': movement.balance_after,
            'reference': movement.reference,
            'reason_code': movement.reason_code,
            'department': movement.department.name if movement.department else None,
            'created_at': movement_date.isoformat() if movement_date else None
        })

    active_assets_views = []
    for tracking in AssetTracking.query.filter_by(status='OUT').order_by(
        AssetTracking.borrowed_at.desc()
    ).all():
        cost = (
            Decimal(tracking.unit_cost_at_issue)
            if tracking.unit_cost_at_issue is not None
            else item_unit_cost(tracking.item)
        )
        value = movement_value(tracking.quantity, cost)
        active_assets_views.append({
            'id': tracking.id,
            'tracking_id': tracking.id,
            'transfer_id': tracking.transfer_id,
            'source': 'transfer' if tracking.transfer_id else 'direct',
            'item': tracking.item.name,
            'department': tracking.department.name,
            'borrower': tracking.borrower_name,
            'quantity': tracking.quantity,
            'unit_of_measure': tracking.item.unit_of_measure,
            'unit_cost': str(cost) if cost is not None else None,
            'value': str(value) if value is not None else None,
            'borrowed_at': tracking.borrowed_at.isoformat() if tracking.borrowed_at else None,
            'status': tracking.status
        })
    # Legacy multi-item transfers created before custody rows were linked still
    # need to appear in the single inventory custody view.
    for transfer in AssetTransfer.query.filter(
        AssetTransfer.status.in_({'Active', 'Discrepancy'})
    ).all():
        for line in TransferItem.query.filter_by(transfer_id=transfer.id).all():
            if line.asset_tracking_id or not line.item_id:
                continue
            remaining = (
                line.sent - line.returned_good - line.damaged - line.missing
            )
            if remaining <= 0:
                continue
            inventory_item = db.session.get(InventoryItem, line.item_id)
            if not inventory_item:
                continue
            cost = (
                Decimal(line.unit_cost_at_issue)
                if line.unit_cost_at_issue is not None
                else item_unit_cost(inventory_item)
            )
            value = movement_value(remaining, cost)
            active_assets_views.append({
                'id': f"transfer-{transfer.id}-{line.id}",
                'tracking_id': None,
                'transfer_id': transfer.id,
                'source': 'transfer',
                'item': inventory_item.name,
                'department': (
                    transfer.department.name if transfer.department
                    else transfer.destination
                ),
                'borrower': transfer.custodian,
                'quantity': remaining,
                'unit_of_measure': inventory_item.unit_of_measure,
                'unit_cost': str(cost) if cost is not None else None,
                'value': str(value) if value is not None else None,
                'borrowed_at': transfer.date_sent,
                'status': transfer.status
            })
    active_assets_views.sort(
        key=lambda asset: str(asset.get('borrowed_at') or ''), reverse=True
    )
    assets_out_value = sum(
        (
            Decimal(asset['value'])
            for asset in active_assets_views
            if asset['value'] is not None
        ),
        Decimal('0.00')
    )

    consumption_rows = db.session.query(
        Department.name,
        db.func.count(StockMovement.id),
        db.func.coalesce(db.func.sum(StockMovement.quantity), 0),
        db.func.coalesce(db.func.sum(StockMovement.total_value), 0)
    ).select_from(StockMovement).join(
        Department, StockMovement.department_id == Department.id
    ).join(
        InventoryItem, StockMovement.item_id == InventoryItem.id
    ).filter(
        StockMovement.movement_type == 'OUT',
        InventoryItem.is_consumable.is_(True),
        StockMovement.department_id.isnot(None)
    ).group_by(Department.name).order_by(
        db.func.coalesce(db.func.sum(StockMovement.total_value), 0).desc()
    ).all()
    consumption_total_value = Decimal('0.00')
    consumption_by_department = []
    for name, count, quantity, value in consumption_rows:
        row_value = Decimal(str(value or 0))
        consumption_total_value += row_value
        consumption_by_department.append({
            'department': name,
            'movements': count,
            'quantity': int(quantity or 0),
            'value': str(row_value)
        })

    return {
        'categories': INVENTORY_CATEGORIES,
        'items': item_views,
        'summary': {
            'total_value': str(total_value),
            'assets_out_value': str(assets_out_value),
            'consumable_value': str(consumable_value),
            'durable_value': str(durable_value),
            'low_stock_count': low_stock_count,
            'low_stock_value': str(low_stock_value),
            'pending_requisition_count': sum(
                requisition.status == 'PENDING' for requisition in requisitions
            ),
            'pending_requisition_value': str(pending_requisition_value),
            'cost_missing_count': cost_missing_count,
            'consumption_total_value': str(consumption_total_value)
        },
        'valuation_by_category': sorted(
            [
                {
                    'category': bucket['category'],
                    'items': bucket['items'],
                    'quantity': bucket['quantity'],
                    'value': str(bucket['value'])
                }
                for bucket in valuation_by_category.values()
            ],
            key=lambda bucket: Decimal(bucket['value']),
            reverse=True
        ),
        'departments': [{
            'id': department.id,
            'name': department.name
        } for department in departments],
        'movements': movement_views,
        'requisitions': requisition_views,
        'active_assets': active_assets_views,
        'consumption_by_department': consumption_by_department,
        'recovery_cases': [{
            'id': tracking.id,
            'item': tracking.item.name,
            'department': tracking.department.name,
            'borrower': tracking.borrower_name,
            'quantity': tracking.quantity,
            'status': tracking.status,
            'recovery_expense_id': tracking.recovery_expense_id,
            'recovery_receivable_id': (
                recovery_receivable_by_tracking[tracking.id].id
                if tracking.id in recovery_receivable_by_tracking else None
            ),
            'recovery_receivable_reference': (
                recovery_receivable_by_tracking[tracking.id].reference
                if tracking.id in recovery_receivable_by_tracking else None
            ),
            'unit_cost': (
                str(tracking.unit_cost_at_issue)
                if tracking.unit_cost_at_issue is not None
                else (
                    str(item_unit_cost(tracking.item))
                    if item_unit_cost(tracking.item) is not None else None
                )
            )
        } for tracking in recovery_cases]
    }

@app.route('/export_inventory_csv')
def export_inventory_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Item ID', 'Item', 'Category', 'Subcategory', 'Type', 'Unit',
        'On Hand', 'Reorder Level', 'Unit Cost', 'Stock Value', 'Department'
    ])
    total_value = Decimal('0.00')
    for item in InventoryItem.query.order_by(InventoryItem.name.asc()).all():
        stock_value = inventory_item_value(item)
        if stock_value is not None:
            total_value += stock_value
        writer.writerow([
            spreadsheet_safe_cell(cell)
            for cell in (
                item.id,
                item.name,
                item.category,
                item.subcategory,
                'Consumable' if item.is_consumable else 'Durable asset',
                item.unit_of_measure,
                item.current_stock,
                item.reorder_level,
                f"{Decimal(item.unit_cost):.2f}" if item.unit_cost is not None else '',
                f"{stock_value:.2f}" if stock_value is not None else '',
                item.department.name if item.department else ''
            )
        ])
    writer.writerow([])
    writer.writerow(['', '', '', '', '', '', '', '', 'Total', f"{total_value:.2f}", ''])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
            'attachment;filename=thika_gymkhana_inventory_valuation.csv'
        }
    )

@app.route('/export_fixed_assets_csv')
def export_fixed_assets_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Asset Tag', 'Name', 'Category', 'Subcategory', 'Status', 'Serial',
        'Acquisition Date', 'Acquisition Cost', 'Quantity', 'Salvage Value',
        'Useful Life Months', 'Monthly Depreciation', 'Accumulated Depreciation',
        'Net Book Value', 'Department', 'Location', 'Custodian', 'Supplier',
        'Disposal Date', 'Disposal Proceeds', 'Gain / Loss'
    ])
    total_cost = Decimal('0.00')
    total_accumulated = Decimal('0.00')
    total_nbv = Decimal('0.00')
    for asset in FixedAsset.query.order_by(FixedAsset.asset_tag.asc()).all():
        cost = Decimal(str(asset.acquisition_cost))
        accumulated = Decimal(str(asset.accumulated_depreciation))
        total_cost += cost
        total_accumulated += accumulated
        total_nbv += asset.net_book_value
        writer.writerow([
            spreadsheet_safe_cell(cell)
            for cell in (
                asset.asset_tag,
                asset.name,
                asset.category,
                asset.subcategory or '',
                asset.status,
                asset.serial_number or '',
                asset.acquisition_date.isoformat() if asset.acquisition_date else '',
                f"{cost:.2f}",
                asset.quantity,
                f"{Decimal(str(asset.salvage_value)):.2f}",
                asset.useful_life_months,
                f"{asset.monthly_depreciation:.2f}",
                f"{accumulated:.2f}",
                f"{asset.net_book_value:.2f}",
                asset.department.name if asset.department else '',
                asset.location or '',
                asset.custodian or '',
                asset.supplier.name if asset.supplier else '',
                asset.disposal_date.isoformat() if asset.disposal_date else '',
                (
                    f"{Decimal(str(asset.disposal_proceeds)):.2f}"
                    if asset.disposal_proceeds is not None else ''
                ),
                (
                    f"{Decimal(str(asset.gain_loss_on_disposal)):.2f}"
                    if asset.gain_loss_on_disposal is not None else ''
                )
            )
        ])
    writer.writerow([])
    writer.writerow([
        '', '', '', '', '', '', '', f"{total_cost:.2f}", '', '', '', '',
        f"{total_accumulated:.2f}", f"{total_nbv:.2f}"
    ])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
            'attachment;filename=thika_gymkhana_fixed_assets.csv'
        }
    )

@app.route('/export_depreciation_csv')
def export_depreciation_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Period', 'Asset Tag', 'Asset Name', 'Amount',
        'Accumulated After', 'Created At', 'Created By'
    ])
    entries = DepreciationEntry.query.order_by(
        DepreciationEntry.period.desc(),
        DepreciationEntry.id.desc()
    ).all()
    for entry in entries:
        writer.writerow([
            spreadsheet_safe_cell(cell)
            for cell in (
                entry.period,
                entry.fixed_asset.asset_tag if entry.fixed_asset else '',
                entry.fixed_asset.name if entry.fixed_asset else '',
                f"{Decimal(str(entry.amount)):.2f}",
                f"{Decimal(str(entry.accumulated_after)):.2f}",
                entry.created_at.isoformat() if entry.created_at else '',
                entry.created_by or ''
            )
        ])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
            'attachment;filename=thika_gymkhana_depreciation.csv'
        }
    )

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
                'expenses': [
                    serialize_expense(
                        expense,
                        payments_by_expense.get(expense.id, [])
                    )
                    for expense in filtered
                ],
                'suppliers': [{
                    'id': supplier.id,
                    'name': supplier.name,
                    'contact_person': supplier.contact_person,
                    'phone': supplier.phone,
                    'email': supplier.email,
                    'address': supplier.address,
                    'is_active': supplier.is_active
                } for supplier in Supplier.query.order_by(Supplier.name).all()],
                'departments': [{
                    'id': department.id,
                    'name': department.name
                } for department in Department.query.order_by(Department.name).all()],
                'categories': list(PROCUREMENT_CATEGORIES),
                'inventory_items': [{
                    'id': item.id,
                    'name': item.name,
                    'category': item.category,
                    'subcategory': item.subcategory,
                    'unit_of_measure': item.unit_of_measure,
                    'current_stock': item.current_stock,
                    'unit_cost': (
                        str(item_unit_cost(item))
                        if item_unit_cost(item) is not None else None
                    ),
                    'is_consumable': item.is_consumable
                } for item in InventoryItem.query.order_by(InventoryItem.name).all()],
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
        spend_by_supplier = {}
        spend_by_department = {}
        spend_by_category = {}
        receipt_status_counts = {}
        match_status_counts = {}
        price_variance_total = Decimal('0.00')
        price_variance_records = []
        received_value_total = Decimal('0.00')
        outstanding_receipt_value = Decimal('0.00')
        for expense in expenses:
            receipt_status = (
                expense.receipt_status or expense_receipt_status(expense)
            )
            receipt_status_counts[receipt_status] = (
                receipt_status_counts.get(receipt_status, 0) + 1
            )
            match_status = expense_match_status(expense)
            match_status_counts[match_status] = (
                match_status_counts.get(match_status, 0) + 1
            )
            received = expense_received_value(expense)
            received_value_total += received
            outstanding_receipt_value += sum(
                (
                    Decimal(str(line.unit_cost))
                    * max(expense_line_outstanding(line), 0)
                    for line in expense.lines
                ),
                Decimal('0.00')
            )
            variance = expense_price_variance(expense)
            price_variance_total += variance
            if variance != Decimal('0.00'):
                price_variance_records.append({
                    'id': expense.id,
                    'date': expense.date_created,
                    'supplier': (
                        expense.supplier.name if expense.supplier
                        else 'Unassigned supplier'
                    ),
                    'amount': str(variance)
                })
            expense_payments = payments_by_expense.get(expense.id, [])
            paid_total = sum(
                (Decimal(str(payment['amount'])) for payment in expense_payments),
                Decimal('0.00')
            )
            supplier_name = (
                expense.supplier.name if expense.supplier
                else 'Unassigned supplier'
            )
            department_name = (
                expense.department.name if expense.department
                else 'Unassigned department'
            )
            spend_by_supplier[supplier_name] = (
                spend_by_supplier.get(supplier_name, Decimal('0.00')) + paid_total
            )
            spend_by_department[department_name] = (
                spend_by_department.get(department_name, Decimal('0.00'))
                + paid_total
            )
            ordered_total = expense_ordered_value(expense)
            if ordered_total > 0 and paid_total > 0:
                for line in expense.lines:
                    share = (
                        paid_total * Decimal(str(line.line_total)) / ordered_total
                    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    spend_by_category[line.category] = (
                        spend_by_category.get(line.category, Decimal('0.00'))
                        + share
                    )
            elif paid_total > 0:
                spend_by_category['General'] = (
                    spend_by_category.get('General', Decimal('0.00'))
                    + paid_total
                )
        return {
            **{name: str(amount) for name, amount in totals.items()},
            'status_counts': status_counts,
            'receipt_status_counts': receipt_status_counts,
            'match_status_counts': match_status_counts,
            'received_value_total': str(received_value_total),
            'outstanding_receipt_value': str(outstanding_receipt_value),
            'price_variance_total': str(price_variance_total),
            'price_variance_records': price_variance_records,
            'spend_by_supplier': [
                {'name': name, 'amount': str(amount)}
                for name, amount in sorted(
                    spend_by_supplier.items(),
                    key=lambda pair: pair[1], reverse=True
                )
            ],
            'spend_by_department': [
                {'name': name, 'amount': str(amount)}
                for name, amount in sorted(
                    spend_by_department.items(),
                    key=lambda pair: pair[1], reverse=True
                )
            ],
            'spend_by_category': [
                {'name': name, 'amount': str(amount)}
                for name, amount in sorted(
                    spend_by_category.items(),
                    key=lambda pair: pair[1], reverse=True
                )
            ],
            'cost_centers': [
                {'name': name, 'amount': str(amount)}
                for name, amount in sorted(
                    cost_centers.items(), key=lambda pair: pair[1], reverse=True
                )
            ],
            'records': records
        }

    if section == 'recovery-receivables':
        receivables = RecoveryReceivable.query.order_by(
            RecoveryReceivable.created_at.desc()
        ).all()
        today = datetime.now().date()
        rows = []
        summary_outstanding = Decimal('0.00')
        summary_overdue_amount = Decimal('0.00')
        summary_settled = Decimal('0.00')
        summary_written_off = Decimal('0.00')
        open_count = 0
        overdue_count = 0
        settled_count = 0
        written_off_count = 0
        aging = {
            'current': Decimal('0.00'),
            '1_30': Decimal('0.00'),
            '31_60': Decimal('0.00'),
            '61_90': Decimal('0.00'),
            'over_90': Decimal('0.00')
        }
        for receivable in receivables:
            balance = receivable.balance
            settled = Decimal(str(receivable.amount_settled))
            amount = Decimal(str(receivable.amount))
            summary_settled += settled
            days_overdue = 0
            if receivable.due_date and receivable.due_date < today:
                days_overdue = (today - receivable.due_date).days
            if receivable.status in {'OPEN', 'PARTIALLY_SETTLED'}:
                open_count += 1
                summary_outstanding += balance
                if days_overdue > 0:
                    overdue_count += 1
                    summary_overdue_amount += balance
                    if days_overdue <= 30:
                        aging['1_30'] += balance
                    elif days_overdue <= 60:
                        aging['31_60'] += balance
                    elif days_overdue <= 90:
                        aging['61_90'] += balance
                    else:
                        aging['over_90'] += balance
                else:
                    aging['current'] += balance
            elif receivable.status == 'SETTLED':
                settled_count += 1
            elif receivable.status == 'WRITTEN_OFF':
                written_off_count += 1
                summary_written_off += max(amount - settled, Decimal('0.00'))
            rows.append({
                'id': receivable.id,
                'reference': receivable.reference,
                'asset_tracking_id': receivable.asset_tracking_id,
                'transfer_id': receivable.transfer_id,
                'fixed_asset_id': receivable.fixed_asset_id,
                'legacy_expense_id': receivable.legacy_expense_id,
                'borrower': receivable.borrower_name,
                'department_id': receivable.department_id,
                'department': (
                    receivable.department.name if receivable.department else None
                ),
                'description': receivable.description,
                'basis': receivable.basis,
                'amount': str(amount),
                'amount_settled': str(settled),
                'balance': str(balance),
                'issue_date': (
                    receivable.issue_date.isoformat()
                    if receivable.issue_date else None
                ),
                'due_date': (
                    receivable.due_date.isoformat()
                    if receivable.due_date else None
                ),
                'status': receivable.status,
                'is_overdue': receivable.is_overdue,
                'days_overdue': days_overdue,
                'notes': receivable.notes,
                'written_off_at': (
                    receivable.written_off_at.isoformat()
                    if receivable.written_off_at else None
                ),
                'written_off_by': receivable.written_off_by,
                'write_off_reason': receivable.write_off_reason,
                'created_by': receivable.created_by,
                'created_at': (
                    receivable.created_at.isoformat()
                    if receivable.created_at else None
                ),
                'settlements': [{
                    'id': settlement.id,
                    'amount': str(settlement.amount),
                    'settled_on': (
                        settlement.settled_on.isoformat()
                        if settlement.settled_on else None
                    ),
                    'method': settlement.method,
                    'reference': settlement.reference,
                    'notes': settlement.notes,
                    'recorded_by': settlement.recorded_by,
                    'created_at': (
                        settlement.created_at.isoformat()
                        if settlement.created_at else None
                    )
                } for settlement in receivable.settlements]
            })
        return {
            'receivables': rows,
            'summary': {
                'open_count': open_count,
                'outstanding': str(summary_outstanding),
                'overdue_count': overdue_count,
                'overdue_amount': str(summary_overdue_amount),
                'settled_count': settled_count,
                'total_settled': str(summary_settled),
                'written_off_count': written_off_count,
                'written_off_amount': str(summary_written_off)
            },
            'aging': {key: str(value) for key, value in aging.items()},
            'methods': sorted(RECOVERY_SETTLEMENT_METHODS)
        }

    if section == 'fixed-assets':
        assets = FixedAsset.query.order_by(FixedAsset.asset_tag.asc()).all()
        active_assets = [
            asset for asset in assets
            if asset.status in DEPRECIABLE_STATUSES and asset.is_capitalized
        ]
        total_cost = sum(
            (Decimal(str(asset.acquisition_cost)) for asset in active_assets),
            Decimal('0.00')
        )
        total_accumulated = sum(
            (
                Decimal(str(asset.accumulated_depreciation))
                for asset in active_assets
            ),
            Decimal('0.00')
        )
        total_nbv = sum(
            (asset.net_book_value for asset in active_assets),
            Decimal('0.00')
        )
        monthly_depreciation = sum(
            (asset.monthly_depreciation for asset in active_assets),
            Decimal('0.00')
        )
        entries = DepreciationEntry.query.order_by(
            DepreciationEntry.period.desc(),
            DepreciationEntry.id.desc()
        ).limit(250).all()
        periods = sorted(
            {entry.period for entry in DepreciationEntry.query.all()},
            reverse=True
        )
        return {
            'assets': [serialize_fixed_asset(asset) for asset in assets],
            'summary': {
                'total_cost': str(total_cost),
                'total_accumulated': str(total_accumulated),
                'total_nbv': str(total_nbv),
                'monthly_depreciation': str(monthly_depreciation),
                'active_count': len(active_assets),
                'disposed_count': sum(
                    asset.status in {'DISPOSED', 'LOST'} for asset in assets
                ),
                'entry_count': DepreciationEntry.query.count(),
                'current_period': datetime.now().strftime('%Y-%m'),
                'periods': periods
            },
            'depreciation_entries': [{
                'id': entry.id,
                'fixed_asset_id': entry.fixed_asset_id,
                'asset_tag': (
                    entry.fixed_asset.asset_tag if entry.fixed_asset else None
                ),
                'asset_name': (
                    entry.fixed_asset.name if entry.fixed_asset else None
                ),
                'period': entry.period,
                'amount': str(entry.amount),
                'accumulated_after': str(entry.accumulated_after),
                'created_at': (
                    entry.created_at.isoformat() if entry.created_at else None
                ),
                'created_by': entry.created_by
            } for entry in entries],
            'categories': list(FIXED_ASSET_CATEGORIES),
            'departments': [{
                'id': department.id,
                'name': department.name
            } for department in Department.query.order_by(Department.name).all()],
            'suppliers': [{
                'id': supplier.id,
                'name': supplier.name,
                'is_active': supplier.is_active
            } for supplier in Supplier.query.order_by(Supplier.name).all()],
            'inventory_items': [{
                'id': item.id,
                'name': item.name,
                'category': item.category,
                'subcategory': item.subcategory,
                'unit_of_measure': item.unit_of_measure,
                'current_stock': item.current_stock,
                'unit_cost': (
                    str(item_unit_cost(item))
                    if item_unit_cost(item) is not None else None
                )
            } for item in InventoryItem.query.filter_by(
                is_consumable=False
            ).order_by(InventoryItem.name).all()]
        }

    if section == 'transfers':
        transfers = []
        today = datetime.now().date()
        recovery_by_transfer = {
            receivable.transfer_id: receivable.id
            for receivable in RecoveryReceivable.query.filter(
                RecoveryReceivable.transfer_id.isnot(None)
            ).all()
        }
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
                'department_id': transfer.department_id,
                'department': (
                    transfer.department.name if transfer.department
                    else transfer.destination
                ),
                'requested_by': transfer.requested_by,
                'approved_by': transfer.approved_by,
                'reconciled_by': transfer.reconciled_by,
                'custodian': transfer.custodian,
                'return_date': transfer.return_date,
                'status': transfer.status,
                'discrepancy_notes': transfer.discrepancy_notes,
                'recovery_bill_id': transfer.recovery_bill_id,
                'recovery_receivable_id': recovery_by_transfer.get(transfer.id),
                'is_overdue': overdue,
                'items': [{
                    'item_id': item.item_id,
                    'asset_tracking_id': item.asset_tracking_id,
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing,
                    'unit_cost_at_issue': (
                        str(item.unit_cost_at_issue)
                        if item.unit_cost_at_issue is not None else None
                    ),
                    'recovery_value': (
                        str((
                            Decimal(item.unit_cost_at_issue)
                            * (item.damaged + item.missing)
                        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                        if item.unit_cost_at_issue is not None else None
                    )
                } for item in TransferItem.query.filter_by(transfer_id=transfer.id).all()]
            })
        return {
            'transfers': transfers,
            'inventory_items': [{
                'id': item.id,
                'name': item.name,
                'current_stock': item.current_stock,
                'unit_of_measure': item.unit_of_measure,
                'unit_cost': (
                    str(item_unit_cost(item))
                    if item_unit_cost(item) is not None else None
                )
            } for item in InventoryItem.query.filter_by(
                is_consumable=False
            ).order_by(InventoryItem.name).all()],
            'departments': [{
                'id': department.id,
                'name': department.name
            } for department in Department.query.order_by(Department.name).all()],
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
        record_stock_movement(
            item, 'IN', current_stock, 'Initial Stock Opening',
            unit_cost=unit_cost, balance_after=current_stock
        )
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

    duplicate = StockMovement.query.filter(
        StockMovement.item_id == item.id,
        StockMovement.movement_type == 'IN',
        db.func.lower(StockMovement.reference) == reference.lower()
    ).first()
    if duplicate:
        flash(
            "This supplier/invoice reference has already been received for this item.",
            "error"
        )
        return redirect(url_for('inventory_dashboard'))

    receipt_unit_cost = unit_cost
    prior_stock = item.current_stock
    prior_cost = item_unit_cost(item)
    new_stock = prior_stock + quantity
    if prior_stock and prior_cost is not None:
        new_cost = (
            (prior_cost * prior_stock + receipt_unit_cost * quantity)
            / Decimal(new_stock)
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        new_cost = receipt_unit_cost
    # Apply the stock and weighted-average cost in one conditional update. If
    # another receipt changed the same item, this fails instead of losing stock.
    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock == prior_stock
        )
        .values(current_stock=new_stock, unit_cost=new_cost)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Stock changed while receiving. Reload and try again.", "error")
        return redirect(url_for('inventory_dashboard'))
    record_stock_movement(
        item, 'IN', quantity, reference,
        unit_cost=receipt_unit_cost, balance_after=new_stock
    )
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
    reason_code = request.form.get(
        'reason_code', 'COUNT_CORRECTION'
    ).strip().upper()
    item = db.session.get(InventoryItem, item_id) if item_id else None
    if not item:
        flash("Select a valid inventory item.", "error")
        return redirect(url_for('inventory_dashboard'))
    if reason_code not in ADJUSTMENT_REASON_CODES:
        flash("Select a valid adjustment reason code.", "error")
        return redirect(url_for('inventory_dashboard'))
    if counted_stock is None or counted_stock < 0 or not reason or len(reason) > 500:
        flash("Enter a non-negative physical count and adjustment reason.", "error")
        return redirect(url_for('inventory_dashboard'))

    prior_stock = item.current_stock
    difference = counted_stock - prior_stock
    if difference == 0:
        flash("Physical count matches the book balance; no adjustment was posted.", "success")
        return redirect(url_for('inventory_dashboard'))

    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock == prior_stock
        )
        .values(current_stock=counted_stock)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Stock changed while counting. Reload and try again.", "error")
        return redirect(url_for('inventory_dashboard'))
    record_stock_movement(
        item,
        'ADJUSTMENT_IN' if difference > 0 else 'ADJUSTMENT_OUT',
        abs(difference),
        reason,
        unit_cost=item_unit_cost(item),
        balance_after=counted_stock,
        reason_code=reason_code
    )
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

    prior_stock = item.current_stock
    unit_cost = item_unit_cost(item)
    issued_value = movement_value(req.quantity, unit_cost)
    claimed = db.session.execute(
        update(StoreRequisition)
        .where(
            StoreRequisition.id == req.id,
            StoreRequisition.status == 'PENDING'
        )
        .values(
            status='ISSUED',
            issued_by=session.get('user'),
            issued_at=datetime.utcnow(),
            unit_cost_at_issue=unit_cost,
            issued_value=issued_value
        )
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        flash("Requisition has already been processed.", "error")
        return redirect(url_for('inventory_dashboard'))

    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock == prior_stock
        )
        .values(current_stock=prior_stock - req.quantity)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Cannot issue: stock changed or is insufficient.", "error")
        return redirect(url_for('inventory_dashboard'))
    record_stock_movement(
        item, 'OUT', req.quantity, f"Requisition #{req.id}",
        unit_cost=unit_cost,
        balance_after=prior_stock - req.quantity,
        department_id=req.department_id
    )
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

    prior_stock = item.current_stock
    unit_cost = item_unit_cost(item)
    updated = db.session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == item.id,
            InventoryItem.current_stock == prior_stock
        )
        .values(current_stock=prior_stock - quantity)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("Asset transfer failed: stock changed or is insufficient.", "error")
        return redirect(url_for('inventory_dashboard'))
    tracking = AssetTracking(
        item_id=item.id,
        department_id=department.id,
        quantity=quantity,
        borrower_name=borrower,
        status='OUT',
        issued_by=session.get('user'),
        unit_cost_at_issue=unit_cost
    )
    db.session.add(tracking)
    db.session.flush()
    record_stock_movement(
        item, 'OUT', quantity, f"Asset transfer #{tracking.id}",
        unit_cost=unit_cost,
        balance_after=prior_stock - quantity,
        department_id=department.id
    )
    capitalize_tracking_asset(tracking, unit_cost)
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
    if tracking.transfer_id:
        flash(
            "This asset belongs to a multi-item transfer. Reconcile it from the transfers register.",
            "error"
        )
        return redirect(url_for('transfers_panel'))
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
        item = db.session.get(InventoryItem, tracking.item_id)
        if not item:
            db.session.rollback()
            return "Inventory item not found.", 404
        unit_cost = (
            Decimal(tracking.unit_cost_at_issue)
            if tracking.unit_cost_at_issue is not None
            else item_unit_cost(item)
        )
        prior_stock = item.current_stock
        new_stock = prior_stock + tracking.quantity
        updated = db.session.execute(
            update(InventoryItem)
            .where(
                InventoryItem.id == item.id,
                InventoryItem.current_stock == prior_stock
            )
            .values(current_stock=new_stock)
        )
        if updated.rowcount != 1:
            db.session.rollback()
            flash("Stock changed while returning the asset. Reload and try again.", "error")
            return redirect(url_for('inventory_dashboard'))
        record_stock_movement(
            item, 'IN', tracking.quantity, f"Asset return #{tracking.id}",
            unit_cost=unit_cost,
            balance_after=new_stock,
            department_id=tracking.department_id
        )
        flash("Asset returned in good condition and restocked.", "success")
    else:
        flash(
            f"Damage/loss recorded. Recovery workflow flagged for "
            f"{tracking.quantity} {tracking.item.name}.",
            "error"
        )

    fixed_asset = FixedAsset.query.filter_by(
        asset_tracking_id=tracking.id
    ).first()
    if fixed_asset and fixed_asset.is_capitalized:
        if condition == 'GOOD':
            fixed_asset.status = 'IN_STORE'
            fixed_asset.is_capitalized = False
            fixed_asset.decapitalized_at = datetime.utcnow()
            fixed_asset.recovery_basis = None
        else:
            fixed_asset.recovery_basis = fixed_asset.net_book_value
            fixed_asset.status = 'LOST'
            fixed_asset.is_capitalized = False
            fixed_asset.decapitalized_at = datetime.utcnow()

    db.session.commit()
    return redirect(url_for('inventory_dashboard'))

@app.route('/assets/recovery/<int:track_id>', methods=['POST'])
def create_asset_recovery_bill(track_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    tracking = db.session.get(AssetTracking, track_id)
    if not tracking:
        return "Asset tracking record not found.", 404
    if tracking.transfer_id:
        flash(
            "This asset belongs to a multi-item transfer. Use the transfer recovery workflow.",
            "error"
        )
        return redirect(url_for('transfers_panel'))
    if tracking.status != 'DAMAGED_LOST' or not tracking.recovery_triggered:
        flash("Only recorded damaged or lost assets can be sent for recovery.", "error")
        return redirect(url_for('inventory_dashboard'))
    if RecoveryReceivable.query.filter_by(
        asset_tracking_id=tracking.id
    ).first():
        flash("A recovery receivable already exists for this asset record.", "error")
        return redirect(url_for('inventory_dashboard'))
    if tracking.returned_by == session.get('user'):
        flash("A different administrator must raise the recovery receivable.", "error")
        return redirect(url_for('inventory_dashboard'))
    fixed_asset = FixedAsset.query.filter_by(
        asset_tracking_id=tracking.id
    ).first()
    if (
        fixed_asset and fixed_asset.recovery_basis is not None
        and Decimal(str(fixed_asset.recovery_basis)) > 0
    ):
        recovery_amount = Decimal(str(fixed_asset.recovery_basis)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        recovery_basis = 'NBV'
    else:
        unit_cost = (
            Decimal(tracking.unit_cost_at_issue)
            if tracking.unit_cost_at_issue is not None
            else item_unit_cost(tracking.item)
        )
        if unit_cost is None or unit_cost <= 0:
            flash("Set a valid inventory unit cost before estimating recovery.", "error")
            return redirect(url_for('inventory_dashboard'))
        recovery_amount = (
            unit_cost * tracking.quantity
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        recovery_basis = 'ISSUE_COST'
    if not recovery_amount.is_finite() or recovery_amount > Decimal('999999999.99'):
        flash("The asset recovery estimate is outside the supported amount range.", "error")
        return redirect(url_for('inventory_dashboard'))

    reference = generate_prefixed_id('RECOVERY_RECEIVABLE')
    issue_date = datetime.now().date()
    receivable = RecoveryReceivable(
        reference=reference,
        asset_tracking_id=tracking.id,
        fixed_asset_id=fixed_asset.id if fixed_asset else None,
        borrower_name=tracking.borrower_name,
        department_id=tracking.department_id,
        description=(
            f"Asset recovery for {tracking.quantity} "
            f"{tracking.item.unit_of_measure} {tracking.item.name} "
            f"(asset record #{tracking.id})"
        )[:500],
        basis=recovery_basis,
        amount=recovery_amount,
        amount_settled=Decimal('0.00'),
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=30),
        status='OPEN',
        created_by=session.get('user')
    )
    try:
        db.session.add(receivable)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("A recovery receivable already exists for this asset record.", "error")
        return redirect(url_for('inventory_dashboard'))
    flash(
        f"Recovery receivable {reference} raised for {tracking.borrower_name}.",
        "success"
    )
    return redirect(url_for('recovery_receivables_panel'))

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

@app.route('/fixed_assets')
def fixed_assets_panel():
    if 'user' not in session:
        return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template(
        'fixed_assets.html',
        assets=FixedAsset.query.order_by(FixedAsset.asset_tag).all(),
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/recovery_receivables')
def recovery_receivables_panel():
    if 'user' not in session:
        return redirect(url_for('login'))
    if os.path.isfile(os.path.join(app.static_folder, 'react', 'assets', 'index.js')):
        return render_template('react_app.html')
    return render_template(
        'recovery_receivables.html',
        receivables=RecoveryReceivable.query.order_by(
            RecoveryReceivable.created_at.desc()
        ).all(),
        user=session.get('user'),
        role=session.get('role')
    )

@app.route('/fixed_assets/add', methods=['POST'])
def add_fixed_asset():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    subcategory = request.form.get('subcategory', '').strip()
    description = request.form.get('description', '').strip()
    serial_number = request.form.get('serial_number', '').strip()
    location = request.form.get('location', '').strip()
    custodian = request.form.get('custodian', '').strip()
    asset_tag = request.form.get('asset_tag', '').strip().upper()
    inventory_item_id = request.form.get('inventory_item_id', type=int)
    supplier_id = request.form.get('supplier_id', type=int)
    department_id = request.form.get('department_id', type=int)
    acquisition_date_raw = request.form.get('acquisition_date', '').strip()
    raw_cost = request.form.get('acquisition_cost', '').strip()
    raw_salvage = request.form.get('salvage_value', '0').strip()
    raw_quantity = request.form.get('quantity', '1').strip()
    raw_life = request.form.get('useful_life_months', '').strip()
    status = request.form.get('status', 'IN_USE').strip().upper()

    inventory_item = (
        db.session.get(InventoryItem, inventory_item_id)
        if inventory_item_id else None
    )
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else None
    department = db.session.get(Department, department_id) if department_id else None
    if inventory_item_id and (
        not inventory_item or inventory_item.is_consumable
    ):
        flash("Select a valid durable inventory item, or leave the link blank.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if supplier_id and not supplier:
        flash("Select a valid supplier.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if department_id and not department:
        flash("Select a valid department.", "error")
        return redirect(url_for('fixed_assets_panel'))

    if inventory_item:
        name = name or inventory_item.name
        category = category or inventory_item.category
        subcategory = subcategory or inventory_item.subcategory
    category = category or 'Other'
    if category not in FIXED_ASSET_CATEGORIES:
        flash("Select a valid fixed-asset category.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if not name or len(name) > 150:
        flash("Enter a valid asset name.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if (
        len(subcategory) > 60 or len(description) > 500
        or len(serial_number) > 120 or len(location) > 120
        or len(custodian) > 120 or len(asset_tag) > 32
    ):
        flash("One or more asset fields exceed the allowed length.", "error")
        return redirect(url_for('fixed_assets_panel'))

    try:
        acquisition_date = (
            datetime.strptime(acquisition_date_raw, '%Y-%m-%d').date()
            if acquisition_date_raw else datetime.now().date()
        )
    except ValueError:
        flash("Acquisition date must use YYYY-MM-DD format.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if acquisition_date > datetime.now().date():
        flash("Acquisition date cannot be in the future.", "error")
        return redirect(url_for('fixed_assets_panel'))

    try:
        quantity = int(raw_quantity)
        useful_life_months = (
            int(raw_life) if raw_life else fixed_asset_default_life(category)
        )
        salvage_value = Decimal(raw_salvage or '0').quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        acquisition_cost = (
            Decimal(raw_cost).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if raw_cost else None
        )
    except (InvalidOperation, ValueError):
        flash("Enter valid quantity, cost, salvage, and useful-life values.", "error")
        return redirect(url_for('fixed_assets_panel'))

    if acquisition_cost is None and inventory_item:
        item_cost = item_unit_cost(inventory_item)
        if item_cost is not None:
            acquisition_cost = (item_cost * quantity).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
    if (
        acquisition_cost is None or not acquisition_cost.is_finite()
        or acquisition_cost <= 0
        or acquisition_cost > Decimal('999999999.99')
        or quantity <= 0 or quantity > 1000000
        or not salvage_value.is_finite() or salvage_value < 0
        or salvage_value > acquisition_cost
        or useful_life_months <= 0 or useful_life_months > 600
        or status not in {'IN_USE', 'IN_STORE', 'UNDER_REPAIR'}
    ):
        flash(
            "Enter a positive acquisition cost and valid quantity, salvage, "
            "useful life, and status.", "error"
        )
        return redirect(url_for('fixed_assets_panel'))

    if asset_tag:
        if FixedAsset.query.filter(
            db.func.lower(FixedAsset.asset_tag) == asset_tag.lower()
        ).first():
            flash("That asset tag is already in use.", "error")
            return redirect(url_for('fixed_assets_panel'))
    else:
        asset_tag = generate_fixed_asset_tag()

    asset = FixedAsset(
        asset_tag=asset_tag,
        name=name,
        category=category,
        subcategory=subcategory or None,
        description=description or None,
        serial_number=serial_number or None,
        inventory_item_id=inventory_item.id if inventory_item else None,
        supplier_id=supplier.id if supplier else None,
        department_id=department.id if department else None,
        location=location or None,
        custodian=custodian or None,
        acquisition_date=acquisition_date,
        acquisition_cost=acquisition_cost,
        quantity=quantity,
        salvage_value=salvage_value,
        useful_life_months=useful_life_months,
        depreciation_method='STRAIGHT_LINE',
        accumulated_depreciation=Decimal('0.00'),
        status=status,
        created_by=session.get('user')
    )
    db.session.add(asset)
    db.session.commit()
    flash(f"Fixed asset {asset.asset_tag} added to the register.", "success")
    return redirect(url_for('fixed_assets_panel'))

@app.route('/fixed_assets/<int:asset_id>/update', methods=['POST'])
def update_fixed_asset(asset_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    asset = db.session.get(FixedAsset, asset_id)
    if not asset:
        return "Fixed asset not found.", 404
    if asset.status in {'DISPOSED', 'LOST'}:
        flash("Disposed or lost assets cannot be edited.", "error")
        return redirect(url_for('fixed_assets_panel'))

    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    subcategory = request.form.get('subcategory', '').strip()
    description = request.form.get('description', '').strip()
    serial_number = request.form.get('serial_number', '').strip()
    location = request.form.get('location', '').strip()
    custodian = request.form.get('custodian', '').strip()
    department_id = request.form.get('department_id', type=int)
    supplier_id = request.form.get('supplier_id', type=int)
    raw_life = request.form.get('useful_life_months', '').strip()
    raw_salvage = request.form.get('salvage_value', '').strip()
    raw_life = raw_life or str(asset.useful_life_months)
    raw_salvage = raw_salvage or str(asset.salvage_value)
    status = request.form.get('status', asset.status).strip().upper()

    department = db.session.get(Department, department_id) if department_id else None
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else None
    if department_id and not department:
        flash("Select a valid department.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if supplier_id and not supplier:
        flash("Select a valid supplier.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if (
        not name or len(name) > 150 or category not in FIXED_ASSET_CATEGORIES
        or len(subcategory) > 60 or len(description) > 500
        or len(serial_number) > 120 or len(location) > 120
        or len(custodian) > 120 or status not in DEPRECIABLE_STATUSES
    ):
        flash("Enter valid fixed-asset details.", "error")
        return redirect(url_for('fixed_assets_panel'))

    try:
        useful_life_months = int(raw_life)
        salvage_value = Decimal(raw_salvage).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        flash("Enter a valid useful life and salvage value.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if (
        useful_life_months <= 0 or useful_life_months > 600
        or not salvage_value.is_finite() or salvage_value < 0
        or salvage_value > Decimal(str(asset.acquisition_cost))
    ):
        flash("Useful life and salvage value are outside the allowed range.", "error")
        return redirect(url_for('fixed_assets_panel'))

    if Decimal(str(asset.accumulated_depreciation)) > 0 and (
        useful_life_months != asset.useful_life_months
        or salvage_value != Decimal(str(asset.salvage_value))
    ):
        flash(
            "Useful life and salvage value cannot be changed after depreciation has been posted.",
            "error"
        )
        return redirect(url_for('fixed_assets_panel'))

    asset.name = name
    asset.category = category
    asset.subcategory = subcategory or None
    asset.description = description or None
    asset.serial_number = serial_number or None
    asset.location = location or None
    asset.custodian = custodian or None
    asset.department_id = department.id if department else None
    asset.supplier_id = supplier.id if supplier else None
    asset.useful_life_months = useful_life_months
    asset.salvage_value = salvage_value
    asset.status = status
    db.session.commit()
    flash(f"Fixed asset {asset.asset_tag} updated.", "success")
    return redirect(url_for('fixed_assets_panel'))

@app.route('/fixed_assets/<int:asset_id>/dispose', methods=['POST'])
def dispose_fixed_asset(asset_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    asset = db.session.get(FixedAsset, asset_id)
    if not asset:
        return "Fixed asset not found.", 404
    if asset.status in {'DISPOSED', 'LOST'}:
        flash("This asset has already been disposed or written off.", "error")
        return redirect(url_for('fixed_assets_panel'))

    disposal_type = request.form.get('disposal_type', 'DISPOSED').strip().upper()
    disposal_date_raw = request.form.get('disposal_date', '').strip()
    raw_proceeds = request.form.get('disposal_proceeds', '0').strip()
    reason = request.form.get('disposal_reason', '').strip()
    if disposal_type not in {'DISPOSED', 'LOST'}:
        flash("Select a valid disposal type.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if not disposal_date_raw or not is_valid_iso_date(disposal_date_raw):
        flash("Disposal date must use YYYY-MM-DD format.", "error")
        return redirect(url_for('fixed_assets_panel'))
    try:
        disposal_date = datetime.strptime(disposal_date_raw, '%Y-%m-%d').date()
        proceeds = Decimal(raw_proceeds or '0').quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        flash("Enter a valid disposal date and proceeds amount.", "error")
        return redirect(url_for('fixed_assets_panel'))
    if (
        disposal_date < asset.acquisition_date
        or disposal_date > datetime.now().date()
        or not proceeds.is_finite() or proceeds < 0
        or proceeds > Decimal('999999999.99')
        or not reason or len(reason) > 500
    ):
        flash("Enter a valid disposal date, proceeds amount, and reason.", "error")
        return redirect(url_for('fixed_assets_panel'))

    net_book_value = asset.net_book_value
    asset.status = disposal_type
    asset.disposal_date = disposal_date
    asset.disposal_proceeds = proceeds
    asset.disposal_reason = reason
    asset.gain_loss_on_disposal = (proceeds - net_book_value).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    db.session.commit()
    flash(
        f"Fixed asset {asset.asset_tag} marked {disposal_type.lower()} with "
        f"gain/loss of {asset.gain_loss_on_disposal:.2f}.",
        "success"
    )
    return redirect(url_for('fixed_assets_panel'))

@app.route('/fixed_assets/depreciate', methods=['POST'])
def run_depreciation():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    period = request.form.get('period', '').strip()
    if not is_valid_period(period):
        flash("Depreciation period must use YYYY-MM format.", "error")
        return redirect(url_for('fixed_assets_panel'))
    year, month = parse_period(period)
    today = datetime.now()
    if (year, month) > (today.year, today.month):
        flash("Depreciation cannot be posted for a future period.", "error")
        return redirect(url_for('fixed_assets_panel'))
    period_end = period_end_date(period)

    entry_count = 0
    total_amount = Decimal('0.00')
    try:
        for asset in FixedAsset.query.all():
            if asset.status not in DEPRECIABLE_STATUSES or not asset.is_capitalized:
                continue
            if not asset.acquisition_date or asset.acquisition_date > period_end:
                continue
            start_year, start_month = add_period_months(
                asset.acquisition_date.year, asset.acquisition_date.month, 1
            )
            start_period = period_string(start_year, start_month)
            next_p = (
                next_period(asset.last_depreciated_period)
                if asset.last_depreciated_period else start_period
            )
            if not next_p or next_p < start_period:
                next_p = start_period
            if next_p > period:
                continue
            if asset.disposal_date:
                disposal_period = period_string(
                    asset.disposal_date.year, asset.disposal_date.month
                )
                if next_p >= disposal_period:
                    continue
            while next_p and next_p <= period:
                if asset.disposal_date:
                    disposal_period = period_string(
                        asset.disposal_date.year, asset.disposal_date.month
                    )
                    if next_p >= disposal_period:
                        break
                existing = DepreciationEntry.query.filter_by(
                    fixed_asset_id=asset.id, period=next_p
                ).first()
                if existing:
                    asset.last_depreciated_period = next_p
                    next_p = next_period(next_p)
                    continue
                remaining = (
                    asset.depreciable_base
                    - Decimal(str(asset.accumulated_depreciation))
                )
                amount = min(asset.monthly_depreciation, remaining)
                if amount <= 0:
                    break
                asset.accumulated_depreciation = (
                    Decimal(str(asset.accumulated_depreciation)) + amount
                )
                asset.last_depreciated_period = next_p
                db.session.add(DepreciationEntry(
                    fixed_asset_id=asset.id,
                    period=next_p,
                    amount=amount,
                    accumulated_after=asset.accumulated_depreciation,
                    created_by=session.get('user')
                ))
                entry_count += 1
                total_amount += amount
                next_p = next_period(next_p)
        db.session.commit()
    except (IntegrityError, SQLAlchemyError):
        db.session.rollback()
        app.logger.exception("Failed to post fixed-asset depreciation")
        flash("Depreciation could not be posted. No changes were saved.", "error")
        return redirect(url_for('fixed_assets_panel'))

    if entry_count:
        flash(
            f"Posted {entry_count} depreciation entr"
            f"{'y' if entry_count == 1 else 'ies'} totalling "
            f"{total_amount:,.2f} through {period}.",
            "success"
        )
    else:
        flash(f"No depreciation was due through {period}.", "success")
    return redirect(url_for('fixed_assets_panel'))

@app.route('/suppliers/add', methods=['POST'])
def add_supplier():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    name = request.form.get('name', '').strip()
    contact_person = request.form.get('contact_person', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    address = request.form.get('address', '').strip()
    if (
        not name or len(name) > 120
        or len(contact_person) > 120
        or len(phone) > 40
        or len(email) > 120
        or len(address) > 255
    ):
        flash("Enter a valid supplier name and contact details.", "error")
        return redirect(url_for('expenses_panel'))
    existing = Supplier.query.filter(
        db.func.lower(Supplier.name) == name.lower()
    ).first()
    if existing:
        flash("A supplier with this name already exists.", "error")
        return redirect(url_for('expenses_panel'))
    db.session.add(Supplier(
        name=name,
        contact_person=contact_person or None,
        phone=phone or None,
        email=email or None,
        address=address or None
    ))
    db.session.commit()
    flash(f"Supplier '{name}' added.", "success")
    return redirect(url_for('expenses_panel'))

@app.route('/suppliers/<int:supplier_id>/toggle', methods=['POST'])
def toggle_supplier(supplier_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return "Supplier not found.", 404
    supplier.is_active = not supplier.is_active
    db.session.commit()
    flash(
        f"Supplier '{supplier.name}' "
        f"{'activated' if supplier.is_active else 'deactivated'}.",
        "success"
    )
    return redirect(url_for('expenses_panel'))

@app.route('/create_expense_order', methods=['POST'])
def create_expense_order():
    if 'user' not in session:
        return redirect(url_for('login'))

    doc_type = request.form.get('doc_type', 'LPO').strip().upper()
    caretaker_name = request.form.get('caretaker_name', '').strip()
    item_description = request.form.get('item_description', '').strip()
    supplier_id = request.form.get('supplier_id', type=int)
    department_id = request.form.get('department_id', type=int)
    supplier = db.session.get(Supplier, supplier_id) if supplier_id else None
    department = db.session.get(Department, department_id) if department_id else None
    if supplier_id and (not supplier or not supplier.is_active):
        flash("Select a valid active supplier.", "error")
        return redirect(url_for('expenses_panel'))
    if department_id and not department:
        flash("Select a valid department / cost centre.", "error")
        return redirect(url_for('expenses_panel'))
    if doc_type not in {'LPO', 'BILL'} or not caretaker_name or len(caretaker_name) > 100:
        flash("Enter a valid document type and buyer / caretaker.", "error")
        return redirect(url_for('expenses_panel'))

    descriptions = request.form.getlist('line_description[]')
    item_ids = request.form.getlist('line_item_id[]')
    quantities = request.form.getlist('line_quantity[]')
    unit_costs = request.form.getlist('line_unit_cost[]')
    categories = request.form.getlist('line_category[]')
    lines_data = []

    if descriptions:
        if (
            len(quantities) != len(descriptions)
            or len(unit_costs) != len(descriptions)
        ):
            flash("Each purchase line needs a description, quantity, and unit cost.", "error")
            return redirect(url_for('expenses_panel'))
        if len(item_ids) != len(descriptions):
            item_ids = [''] * len(descriptions)
        for index, raw_description in enumerate(descriptions):
            description = raw_description.strip()
            raw_item_id = item_ids[index].strip()
            inventory_item = None
            if raw_item_id:
                try:
                    inventory_item = db.session.get(
                        InventoryItem, int(raw_item_id)
                    )
                except ValueError:
                    inventory_item = None
                if not inventory_item:
                    flash("Select valid inventory items for purchase lines.", "error")
                    return redirect(url_for('expenses_panel'))
            if not description or len(description) > 500:
                flash("Each purchase line needs a valid description.", "error")
                return redirect(url_for('expenses_panel'))
            try:
                quantity = int(quantities[index])
                unit_cost = Decimal(unit_costs[index]).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
            except (InvalidOperation, ValueError):
                flash("Purchase quantities and unit costs must be valid.", "error")
                return redirect(url_for('expenses_panel'))
            if (
                quantity <= 0 or quantity > 1000000
                or not unit_cost.is_finite() or unit_cost <= 0
                or unit_cost > Decimal('99999999.99')
            ):
                flash("Purchase quantities and unit costs must be positive and within range.", "error")
                return redirect(url_for('expenses_panel'))
            category = (
                inventory_item.category if inventory_item
                else (categories[index].strip() if index < len(categories) else '')
            )
            if not category:
                category = 'Services & Other'
            if category not in PROCUREMENT_CATEGORIES and category != 'General':
                flash("Select a valid procurement category for each line.", "error")
                return redirect(url_for('expenses_panel'))
            line_total = (unit_cost * quantity).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            lines_data.append({
                'description': description,
                'inventory_item_id': inventory_item.id if inventory_item else None,
                'category': category,
                'quantity': quantity,
                'unit_cost': unit_cost,
                'line_total': line_total
            })
    else:
        # Backward-compatible single-line form used by the server-rendered page.
        try:
            amount = Decimal(request.form.get('amount', '')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            flash("Enter a valid estimated amount.", "error")
            return redirect(url_for('expenses_panel'))
        if (
            not item_description or len(item_description) > 2000
            or not amount.is_finite() or amount <= 0
            or amount > Decimal('999999999.99')
        ):
            flash("Enter a valid description and amount.", "error")
            return redirect(url_for('expenses_panel'))
        lines_data.append({
            'description': item_description[:500],
            'inventory_item_id': None,
            'category': 'Services & Other',
            'quantity': 1,
            'unit_cost': amount,
            'line_total': amount
        })

    total_amount = sum(
        (line['line_total'] for line in lines_data), Decimal('0.00')
    )
    if total_amount <= 0 or total_amount > Decimal('999999999.99'):
        flash("The purchase total is outside the supported amount range.", "error")
        return redirect(url_for('expenses_panel'))

    stored_doc_type = 'LPO' if doc_type == 'LPO' else 'Bill'
    new_id = generate_prefixed_id('LPO' if doc_type == 'LPO' else 'REQUISITION')
    summary = '; '.join(
        f"{line['description']} x{line['quantity']}" for line in lines_data
    )[:2000]
    requires_receipt = any(
        line['inventory_item_id'] for line in lines_data
    )
    new_exp = Expense(
        id=new_id,
        doc_type=stored_doc_type,
        date_created=datetime.now().strftime('%Y-%m-%d %H:%M'),
        caretaker_name=caretaker_name,
        item_description=summary or item_description,
        estimated_amount=f"{total_amount:.2f}",
        actual_amount='0.00',
        status='Pending Approval',
        supplier_id=supplier.id if supplier else None,
        department_id=department.id if department else None,
        receipt_status='PENDING' if requires_receipt else 'NOT_REQUIRED',
        logged_by=session.get('user')
    )
    for index, line in enumerate(lines_data, start=1):
        new_exp.lines.append(ExpenseLine(
            line_no=index,
            inventory_item_id=line['inventory_item_id'],
            description=line['description'],
            category=line['category'],
            quantity_ordered=line['quantity'],
            unit_cost=line['unit_cost'],
            line_total=line['line_total']
        ))
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
    if expense.doc_type == 'Recovery':
        flash("Legacy recovery documents are managed as recovery receivables.", "error")
        return redirect(url_for('expenses_panel'))
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
    if expense.doc_type == 'Recovery':
        flash("Legacy recovery documents are managed as recovery receivables.", "error")
        return redirect(url_for('expenses_panel'))
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

@app.route('/receive_goods/<exp_id>', methods=['POST'])
def receive_goods(exp_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    expense = db.session.get(Expense, exp_id)
    if not expense:
        return "Purchase document not found.", 404
    if expense.doc_type not in {'LPO', 'Bill'}:
        flash("Goods receipts can only be recorded against purchase documents.", "error")
        return redirect(url_for('expenses_panel'))
    if expense.status in {'Pending Approval', 'Rejected'}:
        flash("Approve this document before receiving goods.", "error")
        return redirect(url_for('expenses_panel'))
    if not expense.lines:
        flash("This document has no purchase lines to receive.", "error")
        return redirect(url_for('expenses_panel'))

    delivery_note_ref = request.form.get('delivery_note_ref', '').strip()
    notes = request.form.get('notes', '').strip()
    received_date_raw = request.form.get('received_date', '').strip()
    if not delivery_note_ref or len(delivery_note_ref) > 100:
        flash("Provide a delivery note / GRN reference.", "error")
        return redirect(url_for('expenses_panel'))
    if len(notes) > 2000:
        flash("Receipt notes must be 2000 characters or fewer.", "error")
        return redirect(url_for('expenses_panel'))
    if received_date_raw and not is_valid_iso_date(received_date_raw):
        flash("Received date must use YYYY-MM-DD format.", "error")
        return redirect(url_for('expenses_panel'))
    duplicate = GoodsReceipt.query.filter(
        GoodsReceipt.expense_id == expense.id,
        db.func.lower(GoodsReceipt.delivery_note_ref) == delivery_note_ref.lower()
    ).first()
    if duplicate:
        flash("This delivery note reference has already been received for this document.", "error")
        return redirect(url_for('expenses_panel'))

    line_ids = request.form.getlist('line_id[]')
    quantities = request.form.getlist('quantity_received[]')
    rejected_quantities = request.form.getlist('quantity_rejected[]')
    unit_costs = request.form.getlist('unit_cost[]')
    variance_reasons = request.form.getlist('variance_reason[]')
    if (
        not line_ids
        or len(line_ids) != len(quantities)
        or len(line_ids) != len(rejected_quantities)
        or len(line_ids) != len(unit_costs)
    ):
        flash("Enter at least one received line with quantity and unit cost.", "error")
        return redirect(url_for('expenses_panel'))

    lines_by_id = {line.id: line for line in expense.lines}
    parsed_lines = []
    seen_line_ids = set()
    for index, raw_line_id in enumerate(line_ids):
        try:
            line_id = int(raw_line_id)
            received_quantity = int(quantities[index] or 0)
            rejected_quantity = int(rejected_quantities[index] or 0)
            unit_cost = Decimal(unit_costs[index]).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, ValueError):
            flash("Received quantities and unit costs must be valid.", "error")
            return redirect(url_for('expenses_panel'))
        line = lines_by_id.get(line_id)
        if not line or line_id in seen_line_ids:
            flash("Select valid purchase lines for this receipt.", "error")
            return redirect(url_for('expenses_panel'))
        seen_line_ids.add(line_id)
        if received_quantity < 0 or rejected_quantity < 0:
            flash("Received and rejected quantities cannot be negative.", "error")
            return redirect(url_for('expenses_panel'))
        if received_quantity + rejected_quantity == 0:
            continue
        outstanding = expense_line_outstanding(line)
        if received_quantity + rejected_quantity > outstanding:
            flash(
                f"Cannot receive more than the outstanding quantity for {line.description}.",
                "error"
            )
            return redirect(url_for('expenses_panel'))
        if (
            not unit_cost.is_finite() or unit_cost <= 0
            or unit_cost > Decimal('99999999.99')
        ):
            flash("Receipt unit costs must be positive and within range.", "error")
            return redirect(url_for('expenses_panel'))
        price_variance = (
            (unit_cost - Decimal(str(line.unit_cost))) * received_quantity
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        variance_reason = (
            variance_reasons[index].strip()
            if index < len(variance_reasons) else ''
        )
        if price_variance != Decimal('0.00') and not variance_reason:
            flash("Explain the price variance for each line before receiving.", "error")
            return redirect(url_for('expenses_panel'))
        if len(variance_reason) > 500:
            flash("Variance reasons must be 500 characters or fewer.", "error")
            return redirect(url_for('expenses_panel'))
        parsed_lines.append((
            line, received_quantity, rejected_quantity, unit_cost,
            price_variance, variance_reason or None
        ))

    if not parsed_lines:
        flash("Enter at least one received quantity.", "error")
        return redirect(url_for('expenses_panel'))

    grn_id = generate_prefixed_id('GRN')
    received_at = (
        datetime.strptime(received_date_raw, '%Y-%m-%d')
        if received_date_raw else datetime.utcnow()
    )
    receipt = GoodsReceipt(
        id=grn_id,
        expense_id=expense.id,
        supplier_id=expense.supplier_id,
        department_id=expense.department_id,
        received_at=received_at,
        received_by=session.get('user'),
        delivery_note_ref=delivery_note_ref,
        notes=notes or None
    )
    db.session.add(receipt)
    db.session.flush()

    for line, received_quantity, rejected_quantity, unit_cost, price_variance, reason in parsed_lines:
        if line.inventory_item_id and received_quantity > 0:
            item = db.session.get(InventoryItem, line.inventory_item_id)
            if not item:
                db.session.rollback()
                flash("An inventory item on this document no longer exists.", "error")
                return redirect(url_for('expenses_panel'))
            prior_stock = item.current_stock
            prior_cost = item_unit_cost(item)
            new_stock = prior_stock + received_quantity
            if prior_stock and prior_cost is not None:
                new_cost = (
                    (prior_cost * prior_stock + unit_cost * received_quantity)
                    / Decimal(new_stock)
                ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            else:
                new_cost = unit_cost
            updated = db.session.execute(
                update(InventoryItem)
                .where(
                    InventoryItem.id == item.id,
                    InventoryItem.current_stock == prior_stock
                )
                .values(current_stock=new_stock, unit_cost=new_cost)
            )
            if updated.rowcount != 1:
                db.session.rollback()
                flash("Stock changed while receiving. Reload and try again.", "error")
                return redirect(url_for('expenses_panel'))
            record_stock_movement(
                item, 'IN', received_quantity, f"GRN {grn_id}",
                unit_cost=unit_cost,
                balance_after=new_stock,
                department_id=expense.department_id,
                expense_id=expense.id,
                goods_receipt_id=grn_id
            )
        receipt.lines.append(GoodsReceiptLine(
            expense_line_id=line.id,
            inventory_item_id=line.inventory_item_id,
            quantity_received=received_quantity,
            quantity_rejected=rejected_quantity,
            unit_cost=unit_cost,
            price_variance=price_variance,
            variance_reason=reason
        ))
        line.quantity_received += received_quantity
        line.quantity_rejected += rejected_quantity

    expense.receipt_status = expense_receipt_status(expense)
    db.session.commit()
    flash(
        f"Goods receipt {grn_id} recorded for {expense.id}.",
        "success"
    )
    return redirect(url_for('expenses_panel'))

@app.route('/fulfill_expense/<exp_id>', methods=['POST'])
def fulfill_expense(exp_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    exp = db.session.get(Expense, exp_id)
    if not exp:
        return "Expense record not found.", 404
    if exp.doc_type == 'Recovery':
        flash("Legacy recovery documents are managed as recovery receivables.", "error")
        return redirect(url_for('expenses_panel'))

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
    if expense_requires_receipt(exp):
        received_value = expense_received_value(exp)
        if received_value <= 0:
            flash("Record a goods receipt before recording payment.", "error")
            return redirect(url_for('expenses_panel'))
        if total_actual > received_value and not variance_reason:
            flash(
                "Payment exceeds the value of goods received. Explain the variance before paying.",
                "error"
            )
            return redirect(url_for('expenses_panel'))
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
        recovery_receivable = RecoveryReceivable.query.filter_by(
            transfer_id=transfer.id
        ).first()
        transfer_views.append(TemplateRecord({
            'id': transfer.id,
            'date_sent': transfer.date_sent,
            'origin': transfer.origin,
            'destination': transfer.destination,
            'department': (
                transfer.department.name if transfer.department
                else transfer.destination
            ),
            'requested_by': transfer.requested_by,
            'approved_by': transfer.approved_by,
            'reconciled_by': transfer.reconciled_by,
            'custodian': transfer.custodian,
            'return_date': transfer.return_date,
            'status': transfer.status,
            'discrepancy_notes': transfer.discrepancy_notes,
            'recovery_bill_id': transfer.recovery_bill_id,
            'recovery_receivable_reference': (
                recovery_receivable.reference if recovery_receivable else None
            ),
            'reconciled_by': transfer.reconciled_by,
            'is_overdue': is_overdue,
            'items': [
                TemplateRecord({
                    'name': item.name,
                    'sent': item.sent,
                    'returned_good': item.returned_good,
                    'damaged': item.damaged,
                    'missing': item.missing,
                    'unit_cost_at_issue': item.unit_cost_at_issue,
                    'recovery_value': (
                        (
                            Decimal(item.unit_cost_at_issue)
                            * (item.damaged + item.missing)
                        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                        if item.unit_cost_at_issue is not None else None
                    )
                })
                for item in TransferItem.query.filter_by(
                    transfer_id=transfer.id
                ).all()
            ]
        }))
    return render_template(
        'transfers.html',
        transfers=transfer_views,
        inventory_items=InventoryItem.query.filter_by(
            is_consumable=False
        ).order_by(InventoryItem.name.asc()).all(),
        departments=Department.query.order_by(Department.name.asc()).all(),
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
    raw_item_names = request.form.getlist('item_name[]')
    quantities = request.form.getlist('qty_sent[]')
    if not all(request.form.get(key, '').strip() for key in (
        'destination', 'requested_by', 'custodian', 'return_date'
    )) or not (raw_item_ids or raw_item_names):
        flash("Complete the transfer details and add at least one item.", "error")
        return redirect(url_for('transfers_panel'))
    return_date = request.form.get('return_date', '').strip()
    if not raw_item_ids and raw_item_names:
        resolved_ids = []
        for raw_name in raw_item_names:
            item = InventoryItem.query.filter(
                db.func.lower(InventoryItem.name) == raw_name.strip().lower(),
                InventoryItem.is_consumable.is_(False)
            ).first()
            if not item:
                flash(f"Unknown durable item: {raw_name}.", "error")
                return redirect(url_for('transfers_panel'))
            resolved_ids.append(str(item.id))
        raw_item_ids = resolved_ids
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

    department_id = request.form.get('department_id', type=int)
    department = db.session.get(Department, department_id) if department_id else None
    if not department:
        destination_name = request.form.get('destination', '').strip()
        department = Department.query.filter(
            db.func.lower(Department.name) == destination_name.lower()
        ).first()
    if not department:
        flash("Select a valid destination department for custody tracking.", "error")
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

    prior_stocks = {
        item_id: item.current_stock for item_id, item in inventory_items.items()
    }
    unit_costs = {
        item_id: item_unit_cost(item) for item_id, item in inventory_items.items()
    }
    new_id = generate_prefixed_id('TRANSFER')
    new_trf = AssetTransfer(
        id=new_id,
        date_sent=datetime.now().strftime('%Y-%m-%d %H:%M'),
        origin=request.form.get('origin', 'Main Store'),
        destination=request.form.get('destination', ''),
        department_id=department.id,
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
        prior_stock = prior_stocks[item_id]
        unit_cost = unit_costs[item_id]
        updated = db.session.execute(
            update(InventoryItem)
            .where(
                InventoryItem.id == item_id,
                InventoryItem.is_consumable.is_(False),
                InventoryItem.current_stock == prior_stock
            )
            .values(current_stock=prior_stock - quantity)
        )
        if updated.rowcount != 1:
            db.session.rollback()
            flash("Transfer failed because stock changed or is insufficient.", "error")
            return redirect(url_for('transfers_panel'))
        tracking = AssetTracking(
            item_id=item_id,
            department_id=department.id,
            transfer_id=new_id,
            quantity=quantity,
            borrower_name=request.form.get('custodian', ''),
            status='OUT',
            issued_by=session.get('user'),
            unit_cost_at_issue=unit_cost
        )
        db.session.add(tracking)
        db.session.flush()
        transfer_line = TransferItem(
            transfer_id=new_id,
            item_id=item_id,
            asset_tracking_id=tracking.id,
            name=item.name,
            sent=quantity,
            unit_cost_at_issue=unit_cost
        )
        db.session.add(transfer_line)
        db.session.flush()
        record_stock_movement(
            item, 'OUT', quantity, f"Transfer {new_id}",
            unit_cost=unit_cost,
            balance_after=prior_stock - quantity,
            department_id=department.id
        )
        capitalize_tracking_asset(
            tracking, unit_cost, transfer_item_id=transfer_line.id
        )

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
        transfer.status == 'Discrepancy' and (
            transfer.recovery_bill_id
            or RecoveryReceivable.query.filter_by(
                transfer_id=transfer.id
            ).first()
        )
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

    for line, good, damaged, missing in return_values:
        unit_cost = (
            Decimal(line.unit_cost_at_issue)
            if line.unit_cost_at_issue is not None else None
        )
        tracking = (
            db.session.get(AssetTracking, line.asset_tracking_id)
            if line.asset_tracking_id else None
        )
        if tracking:
            if unit_cost is None:
                unit_cost = (
                    Decimal(tracking.unit_cost_at_issue)
                    if tracking.unit_cost_at_issue is not None
                    else item_unit_cost(tracking.item)
                )
            tracking.status = (
                'RETURNED_GOOD'
                if good == line.sent and not damaged and not missing
                else 'DAMAGED_LOST'
            )
            tracking.returned_at = datetime.utcnow()
            tracking.inspection_notes = discrepancy_notes or None
            tracking.recovery_triggered = bool(damaged or missing)
            tracking.returned_by = session.get('user')
        fixed_asset = None
        if tracking:
            fixed_asset = FixedAsset.query.filter_by(
                asset_tracking_id=tracking.id
            ).first()
        if not fixed_asset:
            fixed_asset = FixedAsset.query.filter_by(
                transfer_item_id=line.id
            ).first()
        if fixed_asset and fixed_asset.is_capitalized:
            if good == line.sent and not damaged and not missing:
                fixed_asset.status = 'IN_STORE'
                fixed_asset.is_capitalized = False
                fixed_asset.decapitalized_at = datetime.utcnow()
                fixed_asset.recovery_basis = None
            else:
                affected = damaged + missing
                per_unit_nbv = (
                    fixed_asset.net_book_value
                    / Decimal(str(fixed_asset.quantity or 1))
                )
                fixed_asset.recovery_basis = (
                    per_unit_nbv * affected
                ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                fixed_asset.status = 'LOST'
                fixed_asset.is_capitalized = False
                fixed_asset.decapitalized_at = datetime.utcnow()
        line.returned_good = good
        line.damaged = damaged
        line.missing = missing
        if (line.item_id or tracking) and good:
            inventory_item = db.session.get(
                InventoryItem, line.item_id or tracking.item_id
            )
            if not inventory_item:
                db.session.rollback()
                flash("An inventory item on this transfer no longer exists.", "error")
                return redirect(url_for('transfers_panel'))
            if unit_cost is None:
                unit_cost = item_unit_cost(inventory_item)
            prior_stock = inventory_item.current_stock
            new_stock = prior_stock + good
            updated = db.session.execute(
                update(InventoryItem)
                .where(
                    InventoryItem.id == inventory_item.id,
                    InventoryItem.current_stock == prior_stock
                )
                .values(current_stock=new_stock)
            )
            if updated.rowcount != 1:
                db.session.rollback()
                flash("Stock changed while reconciling the transfer. Reload and try again.", "error")
                return redirect(url_for('transfers_panel'))
            record_stock_movement(
                inventory_item, 'IN', good, f"Transfer return {transfer.id}",
                unit_cost=unit_cost,
                balance_after=new_stock,
                department_id=(
                    transfer.department_id
                    or (tracking.department_id if tracking else None)
                )
            )
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
        flash("A recovery can only be raised once for an unresolved discrepancy.", "error")
        return redirect(url_for('transfers_panel'))
    if RecoveryReceivable.query.filter_by(transfer_id=transfer.id).first():
        flash("A recovery receivable already exists for this transfer.", "error")
        return redirect(url_for('transfers_panel'))
    if (
        not transfer.discrepancy_notes
        or transfer.discrepancy_notes == 'N/A'
        or transfer.reconciled_by == session.get('user')
    ):
        flash(
            "A different administrator and a documented discrepancy are required for recovery.",
            "error"
        )
        return redirect(url_for('transfers_panel'))

    lines = TransferItem.query.filter_by(transfer_id=transfer.id).all()
    discrepant = [
        line for line in lines if line.damaged + line.missing > 0
    ]
    if not discrepant:
        flash("This transfer has no damaged or missing items to recover.", "error")
        return redirect(url_for('transfers_panel'))

    total_amount = Decimal('0.00')
    description_parts = []
    used_nbv = True
    first_fixed_asset_id = None
    for line in discrepant:
        fixed_asset = None
        if line.asset_tracking_id:
            fixed_asset = FixedAsset.query.filter_by(
                asset_tracking_id=line.asset_tracking_id
            ).first()
        if not fixed_asset:
            fixed_asset = FixedAsset.query.filter_by(
                transfer_item_id=line.id
            ).first()
        affected = line.damaged + line.missing
        if (
            fixed_asset and fixed_asset.recovery_basis is not None
            and Decimal(str(fixed_asset.recovery_basis)) > 0
        ):
            line_amount = Decimal(str(fixed_asset.recovery_basis)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            if first_fixed_asset_id is None:
                first_fixed_asset_id = fixed_asset.id
        else:
            used_nbv = False
            unit_cost = (
                Decimal(line.unit_cost_at_issue)
                if line.unit_cost_at_issue is not None else None
            )
            if unit_cost is None and line.asset_tracking_id:
                tracking = db.session.get(AssetTracking, line.asset_tracking_id)
                if tracking and tracking.unit_cost_at_issue is not None:
                    unit_cost = Decimal(tracking.unit_cost_at_issue)
            if unit_cost is None:
                unit_cost = item_unit_cost(line.inventory_item)
            if unit_cost is None or unit_cost <= 0:
                flash(
                    f"Set a valid unit cost for {line.name} before creating recovery.",
                    "error"
                )
                return redirect(url_for('transfers_panel'))
            line_amount = (
                unit_cost * affected
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total_amount += line_amount
        if total_amount > Decimal('999999999.99'):
            flash("The recovery estimate is outside the supported amount range.", "error")
            return redirect(url_for('transfers_panel'))
        description_parts.append(f"{line.name} x{affected}")

    reference = generate_prefixed_id('RECOVERY_RECEIVABLE')
    issue_date = datetime.now().date()
    receivable = RecoveryReceivable(
        reference=reference,
        transfer_id=transfer.id,
        fixed_asset_id=first_fixed_asset_id,
        borrower_name=transfer.custodian,
        department_id=transfer.department_id,
        description=(
            f"Asset recovery for transfer {transfer.id}: "
            + ", ".join(description_parts)
        )[:500],
        basis='NBV' if used_nbv else 'ISSUE_COST',
        amount=total_amount,
        amount_settled=Decimal('0.00'),
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=30),
        status='OPEN',
        created_by=session.get('user')
    )
    try:
        db.session.add(receivable)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("A recovery receivable already exists for this transfer.", "error")
        return redirect(url_for('transfers_panel'))
    flash(
        f"Recovery receivable {reference} raised for {transfer.custodian}.",
        "success"
    )
    return redirect(url_for('recovery_receivables_panel'))

@app.route('/recovery_receivables/<int:receivable_id>/settle', methods=['POST'])
def settle_recovery_receivable(receivable_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    receivable = db.session.get(RecoveryReceivable, receivable_id)
    if not receivable:
        return "Recovery receivable not found.", 404
    if receivable.status in {'SETTLED', 'WRITTEN_OFF'}:
        flash("This receivable is already closed.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    raw_amount = request.form.get('amount', '').strip()
    settled_on_raw = request.form.get('settled_on', '').strip()
    method = request.form.get('method', 'CASH').strip().upper()
    reference = request.form.get('reference', '').strip()
    notes = request.form.get('notes', '').strip()
    try:
        amount = Decimal(raw_amount).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        flash("Enter a valid settlement amount.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    if (
        not amount.is_finite() or amount <= 0
        or amount > Decimal('999999999.99')
    ):
        flash("Settlement amount must be positive and within range.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    balance = receivable.balance
    if amount > balance:
        flash(f"Settlement exceeds the outstanding balance of {balance:.2f}.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    if settled_on_raw:
        if not is_valid_iso_date(settled_on_raw):
            flash("Settlement date must use YYYY-MM-DD format.", "error")
            return redirect(url_for('recovery_receivables_panel'))
        settled_on = datetime.strptime(settled_on_raw, '%Y-%m-%d').date()
        if settled_on > datetime.now().date():
            flash("Settlement date cannot be in the future.", "error")
            return redirect(url_for('recovery_receivables_panel'))
    else:
        settled_on = datetime.now().date()
    if method not in RECOVERY_SETTLEMENT_METHODS:
        flash("Select a valid settlement method.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    if len(reference) > 100 or len(notes) > 500:
        flash("Settlement reference or notes exceed the allowed length.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    if not reference and not notes:
        flash("Provide a settlement reference or note.", "error")
        return redirect(url_for('recovery_receivables_panel'))

    current_settled = Decimal(str(receivable.amount_settled))
    new_settled = current_settled + amount
    new_status = (
        'SETTLED'
        if new_settled >= Decimal(str(receivable.amount))
        else 'PARTIALLY_SETTLED'
    )
    updated = db.session.execute(
        update(RecoveryReceivable)
        .where(
            RecoveryReceivable.id == receivable.id,
            RecoveryReceivable.amount_settled == current_settled,
            RecoveryReceivable.status.in_({'OPEN', 'PARTIALLY_SETTLED'})
        )
        .values(amount_settled=new_settled, status=new_status)
    )
    if updated.rowcount != 1:
        db.session.rollback()
        flash("This receivable was updated by another administrator. Reload before settling.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    db.session.add(RecoverySettlement(
        receivable_id=receivable.id,
        amount=amount,
        settled_on=settled_on,
        method=method,
        reference=reference or None,
        notes=notes or None,
        recorded_by=session.get('user')
    ))
    db.session.commit()
    flash(
        f"Settlement of {amount:,.2f} recorded against {receivable.reference}.",
        "success"
    )
    return redirect(url_for('recovery_receivables_panel'))

@app.route('/recovery_receivables/<int:receivable_id>/write_off', methods=['POST'])
def write_off_recovery_receivable(receivable_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    receivable = db.session.get(RecoveryReceivable, receivable_id)
    if not receivable:
        return "Recovery receivable not found.", 404
    if receivable.status in {'SETTLED', 'WRITTEN_OFF'}:
        flash("This receivable is already closed.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    reason = request.form.get('reason', '').strip()
    if not reason or len(reason) > 500:
        flash("Provide a write-off reason of 500 characters or fewer.", "error")
        return redirect(url_for('recovery_receivables_panel'))
    receivable.status = 'WRITTEN_OFF'
    receivable.written_off_at = datetime.utcnow()
    receivable.written_off_by = session.get('user')
    receivable.write_off_reason = reason
    db.session.commit()
    flash(f"Recovery receivable {receivable.reference} written off.", "success")
    return redirect(url_for('recovery_receivables_panel'))

@app.route('/export_recovery_receivables_csv')
def export_recovery_receivables_csv():
    if 'user' not in session:
        return redirect(url_for('login'))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Reference', 'Borrower', 'Department', 'Description', 'Basis',
        'Amount', 'Settled', 'Balance', 'Issue Date', 'Due Date',
        'Status', 'Days Overdue', 'Created By'
    ])
    today = datetime.now().date()
    for receivable in RecoveryReceivable.query.order_by(
        RecoveryReceivable.created_at.desc()
    ).all():
        days_overdue = 0
        if receivable.due_date and receivable.due_date < today:
            days_overdue = (today - receivable.due_date).days
        writer.writerow([
            spreadsheet_safe_cell(cell)
            for cell in (
                receivable.reference,
                receivable.borrower_name,
                receivable.department.name if receivable.department else '',
                receivable.description,
                receivable.basis,
                f"{Decimal(str(receivable.amount)):.2f}",
                f"{Decimal(str(receivable.amount_settled)):.2f}",
                f"{receivable.balance:.2f}",
                receivable.issue_date.isoformat() if receivable.issue_date else '',
                receivable.due_date.isoformat() if receivable.due_date else '',
                receivable.status,
                days_overdue,
                receivable.created_by or ''
            )
        ])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
            'attachment;filename=thika_gymkhana_recovery_receivables.csv'
        }
    )

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