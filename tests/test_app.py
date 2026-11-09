import csv
import io
import os
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite://'

import app as app_module
from werkzeug.security import check_password_hash


class SQLiteApplicationTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            app_module.db.session.remove()
            app_module.db.drop_all()
            app_module.db.create_all()
            app_module.db.session.add_all([
                app_module.Service(name='Temporary Guest Fee', price=200.0),
                app_module.User(
                    username='admin',
                    name='Test Administrator',
                    password_hash=app_module.generate_password_hash('admin-pass-123'),
                    role='admin'
                ),
                app_module.User(
                    username='approver',
                    name='Second Administrator',
                    password_hash=app_module.generate_password_hash('approver-pass-123'),
                    role='admin'
                ),
                app_module.User(
                    username='cashier',
                    name='Test Cashier',
                    password_hash=app_module.generate_password_hash('cashier-pass-123'),
                    role='cashier'
                ),
                app_module.Staff(
                    id='STF001',
                    name='Amina Test',
                    department='Housekeeping'
                )
            ])
            app_module.db.session.commit()
        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
            session['display_name'] = 'Test Administrator'

    def tearDown(self):
        with self.app.app_context():
            app_module.db.session.remove()
            app_module.db.drop_all()

    def test_empty_database_pages_render_with_sqlite_fixtures(self):
        today = date.today().isoformat()
        paths = (
            '/',
            '/admin',
            '/inventory',
            '/manage_services',
            '/expenses',
            '/procurement_analytics',
            '/transfers',
            '/hr/attendance',
            '/hr/staff/add',
            '/hr/report',
            '/analytics',
            '/shift_report',
            f'/shift_report?date={today}',
            f'/analytics?date={today}',
            '/export_csv',
            '/hr/export/csv',
            '/hr/export/excel',
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_react_inventory_api_exposes_taxonomy_and_requires_authentication(self):
        response = self.client.get('/api/inventory')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('Housekeeping', payload['categories'])
        self.assertIn('Paper & Tissue', payload['categories']['Housekeeping'])

        unauthenticated_client = self.app.test_client()
        unauthenticated = unauthenticated_client.get('/api/inventory')
        self.assertEqual(unauthenticated.status_code, 401)

    def test_inventory_category_and_subcategory_are_saved_and_validated(self):
        saved = self.client.post('/inventory/add', data={
            'name': 'Hand Soap',
            'category': 'Housekeeping',
            'subcategory': 'Cleaning Supplies',
            'unit_of_measure': 'Litres',
            'is_consumable': 'true',
            'current_stock': '0',
            'reorder_level': '10',
            'unit_cost': ''
        })
        self.assertEqual(saved.status_code, 302)
        with self.app.app_context():
            item = app_module.InventoryItem.query.filter_by(name='Hand Soap').one()
            self.assertEqual(item.category, 'Housekeeping')
            self.assertEqual(item.subcategory, 'Cleaning Supplies')

        invalid = self.client.post('/inventory/add', data={
            'name': 'Invalid Category Pair',
            'category': 'Housekeeping',
            'subcategory': 'Electrical',
            'unit_of_measure': 'Units',
            'is_consumable': 'true',
            'current_stock': '0',
            'reorder_level': '10',
            'unit_cost': ''
        })
        self.assertEqual(invalid.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(app_module.InventoryItem.query.filter_by(
                name='Invalid Category Pair'
            ).first())

    def test_react_login_accepts_json_and_returns_authentication_state(self):
        client = self.app.test_client()
        response = client.post('/login', json={
            'username': 'admin',
            'password': 'admin-pass-123'
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['role'], 'admin')
        self.assertEqual(client.get('/api/session').get_json()['user'], 'admin')

    def test_react_portal_data_endpoints_cover_management_and_reporting(self):
        for section in (
            'admin', 'expenses', 'procurement-analytics', 'transfers',
            'attendance', 'hr-report', 'sales-analytics', 'shift-report'
        ):
            with self.subTest(section=section):
                response = self.client.get(f'/api/portal/{section}')
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.get_json(), dict)

        with self.app.app_context():
            expense = app_module.Expense(
                id='REACT-EXP-001',
                doc_type='LPO',
                date_created=date.today().isoformat(),
                caretaker_name='Storekeeper',
                item_description='Cleaning supplies',
                estimated_amount='1250.00',
                actual_amount='0.00',
                invoice_ref='Pending',
                receipt_file='N/A',
                status='Pending Approval',
                logged_by='cashier'
            )
            app_module.db.session.add(expense)
            app_module.db.session.commit()

        expenses = self.client.get('/api/portal/expenses').get_json()
        self.assertEqual(expenses['expenses'][0]['id'], 'REACT-EXP-001')
        self.assertEqual(expenses['expenses'][0]['payments'], [])

    def test_remaining_authenticated_screens_serve_react_application(self):
        for path in (
            '/expenses', '/transfers', '/admin', '/manage_services',
            '/procurement_analytics', '/fixed_assets', '/recovery_receivables',
            '/hr/attendance', '/hr/staff/add',
            '/hr/report', '/analytics', '/shift_report'
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'/static/react/assets/index.js', response.data)

    def test_inventory_receipts_and_physical_count_adjustments_are_audited(self):
        with self.app.app_context():
            item = app_module.InventoryItem(
                name='Cleaning Liquid',
                current_stock=10,
                unit_cost='1.00'
            )
            app_module.db.session.add(item)
            app_module.db.session.commit()
            item_id = item.id

        received = self.client.post('/inventory/receive', data={
            'item_id': item_id,
            'quantity': 5,
            'unit_cost': '2.00',
            'reference': 'GRN-001 / Supplier Invoice 95'
        })
        self.assertEqual(received.status_code, 302)
        with self.app.app_context():
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            self.assertEqual(item.current_stock, 15)
            self.assertEqual(str(item.unit_cost), '1.33')
            receipt = app_module.StockMovement.query.filter_by(
                movement_type='IN'
            ).one()
            self.assertEqual(receipt.created_by, 'admin')
            self.assertEqual(receipt.reference, 'GRN-001 / Supplier Invoice 95')

        adjusted = self.client.post('/inventory/adjust', data={
            'item_id': item_id,
            'counted_stock': 14,
            'reason': 'Monthly count sheet MC-11'
        })
        self.assertEqual(adjusted.status_code, 302)
        with self.app.app_context():
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            self.assertEqual(item.current_stock, 14)
            adjustment = app_module.StockMovement.query.filter_by(
                movement_type='ADJUSTMENT_OUT'
            ).one()
            self.assertEqual(adjustment.quantity, 1)
            self.assertEqual(adjustment.created_by, 'admin')

        with self.client.session_transaction() as session:
            session['user'] = 'cashier'
            session['role'] = 'cashier'
        self.client.post('/inventory/adjust', data={
            'item_id': item_id,
            'counted_stock': 500,
            'reason': 'Unauthorized adjustment'
        })
        with self.app.app_context():
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                14
            )

    def test_procurement_payments_require_maker_checker_evidence_and_variance_reason(self):
        with self.client.session_transaction() as session:
            session['user'] = 'cashier'
            session['role'] = 'cashier'
        invalid_doc = self.client.post('/create_expense_order', data={
            'doc_type': 'UNAPPROVED',
            'caretaker_name': 'Buyer',
            'item_description': 'Cleaning supplies',
            'amount': '100'
        })
        self.assertEqual(invalid_doc.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.Expense.query.count(), 0)

        created = self.client.post('/create_expense_order', data={
            'doc_type': 'LPO',
            'caretaker_name': 'Buyer',
            'item_description': 'Cleaning supplies',
            'amount': '100.00'
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.one()
            expense_id = expense.id
            self.assertEqual(expense.status, 'Pending Approval')

        payment_data = {
            'actual_amount': '120.00',
            'invoice_ref': 'INV-2026-001'
        }
        same_maker = self.client.post(
            f'/fulfill_expense/{expense_id}',
            data=payment_data
        )
        self.assertEqual(same_maker.status_code, 302)

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        approved = self.client.post(f'/approve_expense/{expense_id}')
        self.assertEqual(approved.status_code, 302)
        no_evidence = self.client.post(
            f'/fulfill_expense/{expense_id}',
            data={'actual_amount': '100.00'}
        )
        self.assertEqual(no_evidence.status_code, 302)
        no_variance_reason = self.client.post(
            f'/fulfill_expense/{expense_id}',
            data=payment_data
        )
        self.assertEqual(no_variance_reason.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.PaymentHistory.query.count(), 0)

        with tempfile.TemporaryDirectory() as upload_dir:
            original_upload_folder = self.app.config['UPLOAD_FOLDER']
            self.app.config['UPLOAD_FOLDER'] = upload_dir
            try:
                recorded = self.client.post(
                    f'/fulfill_expense/{expense_id}',
                    data={
                        **payment_data,
                        'variance_reason': 'Supplier price exceeded estimate; approved by finance',
                        'receipt_file': (io.BytesIO(b'%PDF test receipt'), 'receipt.pdf')
                    },
                    content_type='multipart/form-data'
                )
                self.assertEqual(recorded.status_code, 302)
                with self.app.app_context():
                    payment = app_module.PaymentHistory.query.one()
                    payment_id = payment.id
                    self.assertEqual(payment.paid_by, 'admin')
                    self.assertEqual(payment.variance_reason, 'Supplier price exceeded estimate; approved by finance')
                    self.assertTrue(os.path.isfile(
                        os.path.join(upload_dir, payment.receipt_file)
                    ))
                    expense = app_module.db.session.get(
                        app_module.Expense, expense_id
                    )
                    self.assertEqual(expense.status, 'Fulfilled')
                    self.assertEqual(expense.actual_amount, '120.00')

                receipt_response = self.client.get(
                    f'/expense_payment_receipt/{payment_id}'
                )
                self.assertEqual(receipt_response.status_code, 200)
                receipt_response.close()
                with self.client.session_transaction() as session:
                    session.clear()
                self.assertEqual(
                    self.client.get(
                        f'/expense_payment_receipt/{payment_id}'
                    ).status_code,
                    302
                )

                with self.client.session_transaction() as session:
                    session['user'] = 'admin'
                    session['role'] = 'admin'
                pending_bill = self.client.post('/create_expense_order', data={
                    'doc_type': 'Bill',
                    'caretaker_name': 'Buyer',
                    'item_description': 'Laundry service',
                    'amount': '25.00'
                })
                self.assertEqual(pending_bill.status_code, 302)
                with self.app.app_context():
                    bill = app_module.Expense.query.filter_by(
                        doc_type='Bill'
                    ).one()
                    bill_id = bill.id
                self_approval = self.client.post(
                    f'/approve_expense/{bill_id}'
                )
                self.assertEqual(self_approval.status_code, 302)
                with self.client.session_transaction() as session:
                    session['user'] = 'approver'
                    session['role'] = 'admin'
                rejected = self.client.post(
                    f'/reject_expense/{bill_id}',
                    data={'reason': 'No service order attached'}
                )
                self.assertEqual(rejected.status_code, 302)
                with self.app.app_context():
                    bill = app_module.db.session.get(app_module.Expense, bill_id)
                    self.assertEqual(bill.status, 'Rejected')
                    self.assertEqual(bill.rejection_reason, 'No service order attached')
                self.assertEqual(self.client.get(f'/lpo/{bill_id}').status_code, 302)
            finally:
                self.app.config['UPLOAD_FOLDER'] = original_upload_folder

    def test_inventory_requisition_and_asset_return_workflows(self):
        with self.app.app_context():
            department = app_module.Department(name='Housekeeping')
            consumable = app_module.InventoryItem(
                name='Tissue Paper',
                current_stock=10,
                is_consumable=True
            )
            durable = app_module.InventoryItem(
                name='Plastic Chair',
                current_stock=4,
                is_consumable=False
            )
            app_module.db.session.add_all([department, consumable, durable])
            app_module.db.session.commit()
            department_id = department.id
            consumable_id = consumable.id
            durable_id = durable.id

        request_response = self.client.post('/store/request', data={
            'item_id': consumable_id,
            'department_id': department_id,
            'quantity': 3,
            'requester_name': 'Amina'
        })
        self.assertEqual(request_response.status_code, 302)
        with self.app.app_context():
            requisition = app_module.StoreRequisition.query.one()
            requisition_id = requisition.id
            self.assertEqual(requisition.status, 'PENDING')
            self.assertEqual(app_module.db.session.get(
                app_module.InventoryItem, consumable_id
            ).current_stock, 10)

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        approved = self.client.post(
            f'/store/approve-issue/{requisition_id}'
        )
        self.assertEqual(approved.status_code, 302)
        with self.app.app_context():
            requisition = app_module.db.session.get(
                app_module.StoreRequisition, requisition_id
            )
            self.assertEqual(requisition.status, 'ISSUED')
            self.assertEqual(requisition.item.current_stock, 7)
            self.assertTrue(any(
                movement.movement_type == 'OUT'
                and movement.department_id == department_id
                for movement in requisition.item.movements
            ))

        transferred = self.client.post('/assets/transfer', data={
            'item_id': durable_id,
            'department_id': department_id,
            'quantity': 2,
            'borrower_name': 'Peter'
        })
        self.assertEqual(transferred.status_code, 302)
        with self.app.app_context():
            tracking = app_module.AssetTracking.query.one()
            tracking_id = tracking.id
            self.assertEqual(tracking.item.current_stock, 2)

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        returned = self.client.post(
            f'/assets/return/{tracking_id}',
            data={'condition': 'GOOD', 'inspection_notes': 'Looks good'}
        )
        self.assertEqual(returned.status_code, 302)
        with self.app.app_context():
            tracking = app_module.db.session.get(
                app_module.AssetTracking, tracking_id
            )
            self.assertEqual(tracking.status, 'RETURNED_GOOD')
            self.assertEqual(tracking.item.current_stock, 4)

    def test_asset_loss_requires_separate_recovery_document_maker(self):
        with self.app.app_context():
            department = app_module.Department(name='Events')
            asset = app_module.InventoryItem(
                name='Portable Speaker',
                current_stock=2,
                unit_cost='150.00',
                is_consumable=False
            )
            app_module.db.session.add_all([department, asset])
            app_module.db.session.commit()
            department_id = department.id
            asset_id = asset.id

        transferred = self.client.post('/assets/transfer', data={
            'item_id': asset_id,
            'department_id': department_id,
            'quantity': 1,
            'borrower_name': 'Events Custodian'
        })
        self.assertEqual(transferred.status_code, 302)
        with self.app.app_context():
            tracking = app_module.AssetTracking.query.one()
            tracking_id = tracking.id

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        reported = self.client.post(
            f'/assets/return/{tracking_id}',
            data={
                'condition': 'DAMAGED_LOST',
                'inspection_notes': 'Speaker missing after event'
            }
        )
        self.assertEqual(reported.status_code, 302)
        same_maker = self.client.post(f'/assets/recovery/{tracking_id}')
        self.assertEqual(same_maker.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.RecoveryReceivable.query.count(), 0)
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, asset_id
                ).current_stock,
                1
            )

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        recovery_response = self.client.post(f'/assets/recovery/{tracking_id}')
        self.assertEqual(recovery_response.status_code, 302)
        with self.app.app_context():
            tracking = app_module.db.session.get(
                app_module.AssetTracking, tracking_id
            )
            self.assertIsNone(tracking.recovery_expense_id)
            receivable = app_module.RecoveryReceivable.query.filter_by(
                asset_tracking_id=tracking_id
            ).one()
            self.assertTrue(receivable.reference.startswith('RCV'))
            self.assertEqual(receivable.borrower_name, 'Events Custodian')
            self.assertEqual(Decimal(str(receivable.amount)), Decimal('150.00'))
            self.assertEqual(
                Decimal(str(receivable.amount_settled)), Decimal('0.00')
            )
            self.assertEqual(receivable.status, 'OPEN')
            self.assertEqual(app_module.Expense.query.count(), 0)

    def test_inventory_workflows_reject_wrong_item_types_and_non_admin_approval(self):
        with self.app.app_context():
            department = app_module.Department(name='Bar')
            durable = app_module.InventoryItem(
                name='Bar Stool', current_stock=2, is_consumable=False
            )
            app_module.db.session.add_all([department, durable])
            app_module.db.session.commit()
            department_id = department.id
            durable_id = durable.id
            requisition = app_module.StoreRequisition(
                item_id=durable_id,
                department_id=department_id,
                quantity=1,
                requester_name='Test'
            )
            app_module.db.session.add(requisition)
            app_module.db.session.commit()
            requisition_id = requisition.id

        rejected_request = self.client.post('/store/request', data={
            'item_id': durable_id,
            'department_id': department_id,
            'quantity': 1,
            'requester_name': 'Test'
        })
        self.assertEqual(rejected_request.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.StoreRequisition.query.count(), 1)

        with self.client.session_transaction() as session:
            session['role'] = 'cashier'
        denied_approval = self.client.post(
            f'/store/approve-issue/{requisition_id}'
        )
        self.assertEqual(denied_approval.status_code, 302)
        with self.app.app_context():
            requisition = app_module.db.session.get(
                app_module.StoreRequisition, requisition_id
            )
            self.assertEqual(requisition.status, 'PENDING')
            self.assertEqual(requisition.item.current_stock, 2)

    def test_inventory_receipts_snapshot_value_and_block_duplicate_reference(self):
        with self.app.app_context():
            item = app_module.InventoryItem(
                name='Phase One Supply',
                current_stock=10,
                unit_cost='5.00',
                is_consumable=True
            )
            app_module.db.session.add(item)
            app_module.db.session.commit()
            item_id = item.id

        received = self.client.post('/inventory/receive', data={
            'item_id': item_id,
            'quantity': 5,
            'unit_cost': '7.00',
            'reference': 'GRN-PHASE1'
        })
        self.assertEqual(received.status_code, 302)
        with self.app.app_context():
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            self.assertEqual(item.current_stock, 15)
            self.assertEqual(Decimal(str(item.unit_cost)), Decimal('5.67'))
            movement = app_module.StockMovement.query.filter_by(
                item_id=item_id,
                reference='GRN-PHASE1'
            ).one()
            self.assertEqual(Decimal(str(movement.unit_cost)), Decimal('7.00'))
            self.assertEqual(Decimal(str(movement.total_value)), Decimal('35.00'))
            self.assertEqual(movement.balance_after, 15)

        duplicate = self.client.post('/inventory/receive', data={
            'item_id': item_id,
            'quantity': 5,
            'unit_cost': '7.00',
            'reference': 'grn-phase1'
        })
        self.assertEqual(duplicate.status_code, 302)
        with self.app.app_context():
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            self.assertEqual(item.current_stock, 15)
            self.assertEqual(
                app_module.StockMovement.query.filter_by(
                    item_id=item_id,
                    reference='GRN-PHASE1'
                ).count(),
                1
            )

        adjusted = self.client.post('/inventory/adjust', data={
            'item_id': item_id,
            'counted_stock': 12,
            'reason_code': 'DAMAGE',
            'reason': 'Damaged during handling'
        })
        self.assertEqual(adjusted.status_code, 302)
        with self.app.app_context():
            adjustment = app_module.StockMovement.query.filter_by(
                movement_type='ADJUSTMENT_OUT'
            ).one()
            self.assertEqual(adjustment.reason_code, 'DAMAGE')
            self.assertEqual(Decimal(str(adjustment.unit_cost)), Decimal('5.67'))
            self.assertEqual(Decimal(str(adjustment.total_value)), Decimal('17.01'))
            self.assertEqual(adjustment.balance_after, 12)

    def test_requisition_issue_snapshots_cost_and_value(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase One Store')
            item = app_module.InventoryItem(
                name='Phase One Consumable',
                current_stock=10,
                unit_cost='2.50',
                is_consumable=True
            )
            app_module.db.session.add_all([department, item])
            app_module.db.session.commit()
            department_id = department.id
            item_id = item.id

        requested = self.client.post('/store/request', data={
            'item_id': item_id,
            'department_id': department_id,
            'quantity': 3,
            'requester_name': 'Phase One Requester'
        })
        self.assertEqual(requested.status_code, 302)
        with self.app.app_context():
            requisition_id = app_module.StoreRequisition.query.one().id

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        issued = self.client.post(f'/store/approve-issue/{requisition_id}')
        self.assertEqual(issued.status_code, 302)
        with self.app.app_context():
            requisition = app_module.db.session.get(
                app_module.StoreRequisition, requisition_id
            )
            self.assertEqual(requisition.status, 'ISSUED')
            self.assertIsNotNone(requisition.issued_at)
            self.assertEqual(
                Decimal(str(requisition.unit_cost_at_issue)), Decimal('2.50')
            )
            self.assertEqual(
                Decimal(str(requisition.issued_value)), Decimal('7.50')
            )
            movement = app_module.StockMovement.query.filter_by(
                movement_type='OUT'
            ).one()
            self.assertEqual(Decimal(str(movement.unit_cost)), Decimal('2.50'))
            self.assertEqual(Decimal(str(movement.total_value)), Decimal('7.50'))
            self.assertEqual(movement.balance_after, 7)

    def test_multi_item_transfer_creates_single_custody_ledger_and_snapshot_recovery(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase One Events')
            item = app_module.InventoryItem(
                name='Phase One Projector',
                category='Events & Banqueting',
                subcategory='Audio-Visual Equipment',
                unit_of_measure='Units',
                current_stock=10,
                unit_cost='100.00',
                is_consumable=False
            )
            app_module.db.session.add_all([department, item])
            app_module.db.session.commit()
            department_id = department.id
            item_id = item.id

        created = self.client.post('/create_transfer', data={
            'item_id[]': [str(item_id)],
            'qty_sent[]': ['3'],
            'origin': 'Main Store',
            'destination': 'Phase One Events',
            'department_id': str(department_id),
            'requested_by': 'Events',
            'custodian': 'Caretaker',
            'return_date': (date.today() + timedelta(days=2)).isoformat()
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            transfer = app_module.AssetTransfer.query.one()
            line = app_module.TransferItem.query.one()
            tracking = app_module.AssetTracking.query.one()
            transfer_id = transfer.id
            self.assertEqual(transfer.department_id, department_id)
            self.assertEqual(line.asset_tracking_id, tracking.id)
            self.assertEqual(tracking.transfer_id, transfer.id)
            self.assertEqual(
                Decimal(str(line.unit_cost_at_issue)), Decimal('100.00')
            )
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                7
            )

        inventory = self.client.get('/api/inventory').get_json()
        self.assertEqual(len(inventory['active_assets']), 1)
        self.assertEqual(
            inventory['active_assets'][0]['transfer_id'], transfer_id
        )
        self.assertEqual(inventory['active_assets'][0]['value'], '300.00')
        self.assertEqual(
            Decimal(inventory['summary']['durable_value']), Decimal('700.00')
        )
        transfer_api = self.client.get('/api/portal/transfers').get_json()
        self.assertTrue(transfer_api['departments'])
        self.assertEqual(
            transfer_api['transfers'][0]['items'][0]['unit_cost_at_issue'],
            '100.00'
        )

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        returned = self.client.post(f'/return_transfer/{transfer_id}', data={
            'good_0': '2',
            'damaged_0': '1',
            'discrepancy_notes': 'One projector damaged during use'
        })
        self.assertEqual(returned.status_code, 302)
        with self.app.app_context():
            transfer = app_module.db.session.get(
                app_module.AssetTransfer, transfer_id
            )
            tracking = app_module.AssetTracking.query.one()
            self.assertEqual(transfer.status, 'Discrepancy')
            self.assertEqual(tracking.status, 'DAMAGED_LOST')
            self.assertTrue(tracking.recovery_triggered)
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                9
            )
            # Recovery must use the cost snapshot, not the current item cost.
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            item.unit_cost = Decimal('150.00')
            app_module.db.session.commit()

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        recovery = self.client.post(f'/create_recovery_bill/{transfer_id}')
        self.assertEqual(recovery.status_code, 302)
        with self.app.app_context():
            transfer = app_module.db.session.get(
                app_module.AssetTransfer, transfer_id
            )
            self.assertIsNone(transfer.recovery_bill_id)
            receivable = app_module.RecoveryReceivable.query.filter_by(
                transfer_id=transfer_id
            ).one()
            self.assertTrue(receivable.reference.startswith('RCV'))
            self.assertEqual(
                Decimal(str(receivable.amount)), Decimal('100.00')
            )
            self.assertEqual(receivable.status, 'OPEN')
            self.assertEqual(app_module.Expense.query.count(), 0)

    def test_inventory_api_reports_valuation_consumption_and_csv(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase One Reporting')
            consumable = app_module.InventoryItem(
                name='Phase One Tissue',
                category='Housekeeping',
                subcategory='Paper & Tissue',
                unit_of_measure='Packs',
                current_stock=10,
                unit_cost='3.00',
                is_consumable=True
            )
            durable = app_module.InventoryItem(
                name='Phase One Chair',
                category='Furniture & Appliances',
                subcategory='Furniture',
                unit_of_measure='Units',
                current_stock=2,
                unit_cost='50.00',
                is_consumable=False
            )
            app_module.db.session.add_all([department, consumable, durable])
            app_module.db.session.commit()
            department_id = department.id
            consumable_id = consumable.id

        requested = self.client.post('/store/request', data={
            'item_id': consumable_id,
            'department_id': department_id,
            'quantity': 4,
            'requester_name': 'Reporting Requester'
        })
        self.assertEqual(requested.status_code, 302)
        with self.app.app_context():
            requisition_id = app_module.StoreRequisition.query.one().id
        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        self.client.post(f'/store/approve-issue/{requisition_id}')

        inventory = self.client.get('/api/inventory').get_json()
        self.assertEqual(
            Decimal(inventory['summary']['total_value']), Decimal('118.00')
        )
        self.assertEqual(
            Decimal(inventory['summary']['consumable_value']), Decimal('18.00')
        )
        self.assertEqual(
            Decimal(inventory['summary']['durable_value']), Decimal('100.00')
        )
        self.assertEqual(
            inventory['consumption_by_department'][0]['department'],
            'Phase One Reporting'
        )
        self.assertEqual(
            inventory['consumption_by_department'][0]['quantity'], 4
        )
        self.assertEqual(
            Decimal(inventory['consumption_by_department'][0]['value']),
            Decimal('12.00')
        )
        self.assertTrue(inventory['valuation_by_category'])
        exported = self.client.get('/export_inventory_csv')
        self.assertEqual(exported.status_code, 200)
        csv_text = exported.get_data(as_text=True)
        self.assertIn('Phase One Tissue', csv_text)
        self.assertIn('118.00', csv_text)

    def test_legacy_transfer_lines_appear_in_inventory_custody_view(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase One Legacy')
            item = app_module.InventoryItem(
                name='Phase One Legacy Fan',
                current_stock=3,
                unit_cost='20.00',
                is_consumable=False
            )
            app_module.db.session.add_all([department, item])
            app_module.db.session.commit()
            transfer = app_module.AssetTransfer(
                id='TRFLEGACY1',
                date_sent=f'{date.today().isoformat()} 08:00',
                origin='Main Store',
                destination='Phase One Legacy',
                department_id=department.id,
                requested_by='Legacy Requester',
                approved_by='admin',
                custodian='Legacy Custodian',
                return_date=(date.today() + timedelta(days=2)).isoformat(),
                status='Active'
            )
            app_module.db.session.add(transfer)
            app_module.db.session.commit()
            app_module.db.session.add(app_module.TransferItem(
                transfer_id=transfer.id,
                item_id=item.id,
                name=item.name,
                sent=2
            ))
            app_module.db.session.commit()
            item_id = item.id

        inventory = self.client.get('/api/inventory').get_json()
        self.assertEqual(len(inventory['active_assets']), 1)
        self.assertEqual(inventory['active_assets'][0]['transfer_id'], 'TRFLEGACY1')
        self.assertEqual(inventory['active_assets'][0]['quantity'], 2)
        self.assertEqual(inventory['active_assets'][0]['value'], '40.00')

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        returned = self.client.post('/return_transfer/TRFLEGACY1', data={
            'good_0': '2',
            'damaged_0': '0',
            'discrepancy_notes': ''
        })
        self.assertEqual(returned.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                5
            )
        inventory = self.client.get('/api/inventory').get_json()
        self.assertEqual(inventory['active_assets'], [])

    def test_supplier_and_multi_line_lpo_creation(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase Two Department')
            item = app_module.InventoryItem(
                name='Phase Two Item',
                category='Housekeeping',
                subcategory='Cleaning Supplies',
                unit_of_measure='Units',
                current_stock=0,
                unit_cost='10.00',
                is_consumable=True
            )
            app_module.db.session.add_all([department, item])
            app_module.db.session.commit()
            department_id = department.id
            item_id = item.id

        supplier_response = self.client.post('/suppliers/add', data={
            'name': 'Phase Two Supplier',
            'contact_person': 'Sales Desk',
            'phone': '0700000000',
            'email': 'supplier@example.test',
            'address': 'Thika'
        })
        self.assertEqual(supplier_response.status_code, 302)
        with self.app.app_context():
            supplier = app_module.Supplier.query.one()
            supplier_id = supplier.id
            self.assertEqual(supplier.name, 'Phase Two Supplier')

        duplicate_supplier = self.client.post('/suppliers/add', data={
            'name': 'phase two supplier'
        })
        self.assertEqual(duplicate_supplier.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.Supplier.query.count(), 1)

        created = self.client.post('/create_expense_order', data={
            'doc_type': 'LPO',
            'caretaker_name': 'Phase Two Buyer',
            'supplier_id': supplier_id,
            'department_id': department_id,
            'line_description[]': ['Cleaning fluid', 'Service charge'],
            'line_item_id[]': [str(item_id), ''],
            'line_quantity[]': ['10', '1'],
            'line_unit_cost[]': ['12.50', '100.00'],
            'line_category[]': ['Housekeeping', 'Services & Other']
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.one()
            expense_id = expense.id
            lines = expense.lines
            self.assertEqual(expense.supplier_id, supplier_id)
            self.assertEqual(expense.department_id, department_id)
            self.assertEqual(
                Decimal(expense.estimated_amount), Decimal('225.00')
            )
            self.assertEqual(expense.receipt_status, 'PENDING')
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0].inventory_item_id, item_id)
            self.assertEqual(lines[0].quantity_ordered, 10)
            self.assertEqual(
                Decimal(str(lines[0].unit_cost)), Decimal('12.50')
            )
            self.assertEqual(
                Decimal(str(lines[0].line_total)), Decimal('125.00')
            )
            self.assertEqual(lines[1].category, 'Services & Other')
            self.assertEqual(
                Decimal(str(lines[1].unit_cost)), Decimal('100.00')
            )

        payload = self.client.get('/api/portal/expenses').get_json()
        expense_payload = next(
            row for row in payload['expenses'] if row['id'] == expense_id
        )
        self.assertEqual(expense_payload['supplier'], 'Phase Two Supplier')
        self.assertEqual(expense_payload['department'], 'Phase Two Department')
        self.assertEqual(len(expense_payload['lines']), 2)
        self.assertEqual(expense_payload['receipt_status'], 'PENDING')
        self.assertFalse(expense_payload['can_receive'])
        self.assertEqual(payload['suppliers'][0]['name'], 'Phase Two Supplier')
        self.assertIn('Housekeeping', payload['categories'])
        self.assertEqual(payload['inventory_items'][0]['id'], item_id)

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        self.client.post(f'/approve_expense/{expense_id}')
        printed = self.client.get(f'/lpo/{expense_id}')
        self.assertEqual(printed.status_code, 200)
        printed_text = printed.get_data(as_text=True)
        self.assertIn('Cleaning fluid', printed_text)
        self.assertIn('Service charge', printed_text)
        thermal = self.client.get(f'/lpo_thermal/{expense_id}')
        self.assertEqual(thermal.status_code, 200)
        self.assertIn('Cleaning fluid', thermal.get_data(as_text=True))

    def test_goods_receipt_partial_matching_and_price_variance_controls(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase Two Receiving')
            item = app_module.InventoryItem(
                name='Phase Two Stock',
                category='Food & Beverage',
                subcategory='Food Ingredients',
                unit_of_measure='Units',
                current_stock=0,
                unit_cost='10.00',
                is_consumable=True
            )
            supplier = app_module.Supplier(name='Phase Two Supplier B')
            app_module.db.session.add_all([department, item, supplier])
            app_module.db.session.commit()
            department_id = department.id
            item_id = item.id
            supplier_id = supplier.id

        created = self.client.post('/create_expense_order', data={
            'doc_type': 'LPO',
            'caretaker_name': 'Phase Two Receiver',
            'supplier_id': supplier_id,
            'department_id': department_id,
            'line_description[]': ['Food ingredient'],
            'line_item_id[]': [str(item_id)],
            'line_quantity[]': ['10'],
            'line_unit_cost[]': ['10.00'],
            'line_category[]': ['Food & Beverage']
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.one()
            expense_id = expense.id
            line_id = expense.lines[0].id

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        approved = self.client.post(f'/approve_expense/{expense_id}')
        self.assertEqual(approved.status_code, 302)

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        received = self.client.post(f'/receive_goods/{expense_id}', data={
            'delivery_note_ref': 'DN-PHASE2-1',
            'received_date': date.today().isoformat(),
            'line_id[]': [str(line_id)],
            'quantity_received[]': ['6'],
            'quantity_rejected[]': ['0'],
            'unit_cost[]': ['12.00'],
            'variance_reason[]': ['Supplier price increase']
        })
        self.assertEqual(received.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            line = app_module.db.session.get(app_module.ExpenseLine, line_id)
            item = app_module.db.session.get(app_module.InventoryItem, item_id)
            receipt = app_module.GoodsReceipt.query.one()
            receipt_line = app_module.GoodsReceiptLine.query.one()
            self.assertEqual(expense.receipt_status, 'PARTIALLY_RECEIVED')
            self.assertEqual(line.quantity_received, 6)
            self.assertEqual(item.current_stock, 6)
            self.assertEqual(Decimal(str(item.unit_cost)), Decimal('12.00'))
            self.assertEqual(
                Decimal(str(receipt_line.price_variance)), Decimal('12.00')
            )
            self.assertEqual(
                receipt_line.variance_reason, 'Supplier price increase'
            )
            movement = app_module.StockMovement.query.filter_by(
                goods_receipt_id=receipt.id
            ).one()
            self.assertEqual(movement.expense_id, expense_id)
            self.assertEqual(
                Decimal(str(movement.total_value)), Decimal('72.00')
            )

        over_receipt = self.client.post(f'/receive_goods/{expense_id}', data={
            'delivery_note_ref': 'DN-PHASE2-OVER',
            'received_date': date.today().isoformat(),
            'line_id[]': [str(line_id)],
            'quantity_received[]': ['5'],
            'quantity_rejected[]': ['0'],
            'unit_cost[]': ['10.00'],
            'variance_reason[]': ['']
        })
        self.assertEqual(over_receipt.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.GoodsReceipt.query.count(), 1)
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                6
            )

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        overpayment = self.client.post(f'/fulfill_expense/{expense_id}', data={
            'actual_amount': '80.00',
            'invoice_ref': 'INV-PHASE2-1',
            'service_ref_note': 'Invoice received'
        })
        self.assertEqual(overpayment.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            self.assertEqual(expense.actual_amount, '0.00')
            self.assertEqual(app_module.PaymentHistory.query.count(), 0)

        partial_payment = self.client.post(f'/fulfill_expense/{expense_id}', data={
            'actual_amount': '70.00',
            'invoice_ref': 'INV-PHASE2-1',
            'service_ref_note': 'Part payment against received goods'
        })
        self.assertEqual(partial_payment.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            self.assertEqual(expense.actual_amount, '70.00')
            self.assertEqual(expense.status, 'Partially Paid')
            self.assertEqual(app_module.PaymentHistory.query.count(), 1)

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        final_receipt = self.client.post(f'/receive_goods/{expense_id}', data={
            'delivery_note_ref': 'DN-PHASE2-2',
            'received_date': date.today().isoformat(),
            'line_id[]': [str(line_id)],
            'quantity_received[]': ['4'],
            'quantity_rejected[]': ['0'],
            'unit_cost[]': ['10.00'],
            'variance_reason[]': ['']
        })
        self.assertEqual(final_receipt.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            line = app_module.db.session.get(app_module.ExpenseLine, line_id)
            self.assertEqual(expense.receipt_status, 'RECEIVED')
            self.assertEqual(line.quantity_received, 10)
            self.assertEqual(
                app_module.db.session.get(
                    app_module.InventoryItem, item_id
                ).current_stock,
                10
            )

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        final_payment = self.client.post(f'/fulfill_expense/{expense_id}', data={
            'actual_amount': '30.00',
            'invoice_ref': 'INV-PHASE2-2',
            'service_ref_note': 'Final payment'
        })
        self.assertEqual(final_payment.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            self.assertEqual(expense.actual_amount, '100.00')
            self.assertEqual(expense.status, 'Fulfilled')

        analytics = self.client.get(
            '/api/portal/procurement-analytics'
        ).get_json()
        self.assertEqual(
            Decimal(analytics['price_variance_total']), Decimal('12.00')
        )
        self.assertTrue(any(
            row['name'] == 'Phase Two Supplier B'
            and Decimal(row['amount']) == Decimal('100.00')
            for row in analytics['spend_by_supplier']
        ))
        self.assertTrue(any(
            row['name'] == 'Phase Two Receiving'
            and Decimal(row['amount']) == Decimal('100.00')
            for row in analytics['spend_by_department']
        ))
        self.assertTrue(any(
            row['name'] == 'Food & Beverage'
            and Decimal(row['amount']) == Decimal('100.00')
            for row in analytics['spend_by_category']
        ))
        self.assertTrue(any(
            record['id'] == expense_id
            and Decimal(record['amount']) == Decimal('12.00')
            for record in analytics['price_variance_records']
        ))
        expenses_payload = self.client.get('/api/portal/expenses').get_json()
        expense_payload = next(
            row for row in expenses_payload['expenses']
            if row['id'] == expense_id
        )
        self.assertEqual(expense_payload['match_status'], 'VARIANCE')
        self.assertEqual(
            Decimal(expense_payload['received_value']), Decimal('112.00')
        )
        self.assertEqual(
            Decimal(expense_payload['price_variance']), Decimal('12.00')
        )

    def test_legacy_single_line_lpo_still_pays_without_receipt(self):
        created = self.client.post('/create_expense_order', data={
            'doc_type': 'LPO',
            'caretaker_name': 'Legacy Buyer',
            'item_description': 'Consulting service',
            'amount': '300.00'
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.one()
            expense_id = expense.id
            self.assertEqual(len(expense.lines), 1)
            self.assertEqual(expense.lines[0].category, 'Services & Other')
            self.assertEqual(expense.receipt_status, 'NOT_REQUIRED')

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        self.client.post(f'/approve_expense/{expense_id}')
        paid = self.client.post(f'/fulfill_expense/{expense_id}', data={
            'actual_amount': '300.00',
            'invoice_ref': 'INV-LEGACY-1',
            'service_ref_note': 'Service completed'
        })
        self.assertEqual(paid.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, expense_id)
            self.assertEqual(expense.actual_amount, '300.00')
            self.assertEqual(expense.status, 'Fulfilled')

    def test_fixed_asset_register_depreciation_and_disposal(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase Three Operations')
            app_module.db.session.add(department)
            app_module.db.session.commit()
            department_id = department.id

        created = self.client.post('/fixed_assets/add', data={
            'name': 'Phase Three Generator',
            'category': 'Maintenance & Engineering',
            'subcategory': 'Electrical',
            'description': 'Standby generator',
            'serial_number': 'GEN-PHASE3',
            'location': 'Plant room',
            'custodian': 'Engineer',
            'department_id': str(department_id),
            'acquisition_date': '2025-01-15',
            'acquisition_cost': '1200.00',
            'quantity': '1',
            'salvage_value': '0',
            'useful_life_months': '12',
            'status': 'IN_USE'
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            asset = app_module.FixedAsset.query.one()
            asset_id = asset.id
            asset_tag = asset.asset_tag
            self.assertTrue(asset_tag.startswith('FA'))
            self.assertEqual(
                Decimal(str(asset.monthly_depreciation)), Decimal('100.00')
            )
            self.assertEqual(
                Decimal(str(asset.net_book_value)), Decimal('1200.00')
            )

        depreciation = self.client.post('/fixed_assets/depreciate', data={
            'period': '2025-04'
        })
        self.assertEqual(depreciation.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(app_module.DepreciationEntry.query.count(), 3)
            self.assertEqual(
                Decimal(str(asset.accumulated_depreciation)), Decimal('300.00')
            )
            self.assertEqual(
                Decimal(str(asset.net_book_value)), Decimal('900.00')
            )
            self.assertEqual(asset.last_depreciated_period, '2025-04')

        rerun = self.client.post('/fixed_assets/depreciate', data={
            'period': '2025-04'
        })
        self.assertEqual(rerun.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.DepreciationEntry.query.count(), 3)

        may_run = self.client.post('/fixed_assets/depreciate', data={
            'period': '2025-05'
        })
        self.assertEqual(may_run.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(app_module.DepreciationEntry.query.count(), 4)
            self.assertEqual(
                Decimal(str(asset.accumulated_depreciation)), Decimal('400.00')
            )
            self.assertEqual(
                Decimal(str(asset.net_book_value)), Decimal('800.00')
            )

        disposed = self.client.post(f'/fixed_assets/{asset_id}/dispose', data={
            'disposal_type': 'DISPOSED',
            'disposal_date': '2025-05-15',
            'disposal_proceeds': '850.00',
            'disposal_reason': 'Sold to a contractor'
        })
        self.assertEqual(disposed.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(asset.status, 'DISPOSED')
            self.assertEqual(asset.disposal_date.isoformat(), '2025-05-15')
            self.assertEqual(
                Decimal(str(asset.gain_loss_on_disposal)), Decimal('50.00')
            )
            self.assertEqual(Decimal(str(asset.net_book_value)), Decimal('0.00'))

        after_disposal = self.client.post('/fixed_assets/depreciate', data={
            'period': '2025-06'
        })
        self.assertEqual(after_disposal.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.DepreciationEntry.query.count(), 4)

        api = self.client.get('/api/portal/fixed-assets').get_json()
        self.assertEqual(api['summary']['active_count'], 0)
        self.assertEqual(api['summary']['disposed_count'], 1)
        asset_payload = next(
            item for item in api['assets'] if item['id'] == asset_id
        )
        self.assertEqual(asset_payload['status'], 'DISPOSED')
        self.assertEqual(asset_payload['net_book_value'], '0.00')
        self.assertEqual(asset_payload['gain_loss_on_disposal'], '50.00')
        register_csv = self.client.get('/export_fixed_assets_csv')
        self.assertEqual(register_csv.status_code, 200)
        self.assertIn(asset_tag, register_csv.get_data(as_text=True))
        depreciation_csv = self.client.get('/export_depreciation_csv')
        self.assertEqual(depreciation_csv.status_code, 200)
        self.assertIn('2025-04', depreciation_csv.get_data(as_text=True))

    def test_fixed_asset_update_and_depreciation_basis_lock(self):
        created = self.client.post('/fixed_assets/add', data={
            'name': 'Phase Three Pump',
            'category': 'Maintenance & Engineering',
            'location': 'Old pump room',
            'custodian': 'Old custodian',
            'acquisition_date': '2025-01-15',
            'acquisition_cost': '600.00',
            'quantity': '1',
            'salvage_value': '0',
            'useful_life_months': '12',
            'status': 'IN_USE'
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            asset_id = app_module.FixedAsset.query.one().id

        self.client.post('/fixed_assets/depreciate', data={'period': '2025-02'})
        updated = self.client.post(f'/fixed_assets/{asset_id}/update', data={
            'name': 'Phase Three Pump',
            'category': 'Maintenance & Engineering',
            'subcategory': '',
            'description': 'Updated pump record',
            'serial_number': 'PUMP-PHASE3',
            'location': 'New pump room',
            'custodian': 'New custodian',
            'department_id': '',
            'supplier_id': '',
            'useful_life_months': '12',
            'salvage_value': '0',
            'status': 'IN_STORE'
        })
        self.assertEqual(updated.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(asset.location, 'New pump room')
            self.assertEqual(asset.custodian, 'New custodian')
            self.assertEqual(asset.status, 'IN_STORE')
            self.assertEqual(asset.useful_life_months, 12)

        locked = self.client.post(f'/fixed_assets/{asset_id}/update', data={
            'name': 'Phase Three Pump',
            'category': 'Maintenance & Engineering',
            'subcategory': '',
            'description': 'Updated pump record',
            'serial_number': 'PUMP-PHASE3',
            'location': 'New pump room',
            'custodian': 'New custodian',
            'department_id': '',
            'supplier_id': '',
            'useful_life_months': '24',
            'salvage_value': '50.00',
            'status': 'IN_STORE'
        })
        self.assertEqual(locked.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(asset.useful_life_months, 12)
            self.assertEqual(Decimal(str(asset.salvage_value)), Decimal('0.00'))

        # The UI disables useful life and salvage after depreciation starts.
        # Those fields are omitted from the POST; the server keeps current values.
        metadata_only = self.client.post(f'/fixed_assets/{asset_id}/update', data={
            'name': 'Phase Three Pump',
            'category': 'Maintenance & Engineering',
            'subcategory': '',
            'description': 'Metadata-only update',
            'serial_number': 'PUMP-PHASE3',
            'location': 'Third pump room',
            'custodian': 'New custodian',
            'department_id': '',
            'supplier_id': '',
            'status': 'IN_STORE'
        })
        self.assertEqual(metadata_only.status_code, 302)
        with self.app.app_context():
            asset = app_module.db.session.get(app_module.FixedAsset, asset_id)
            self.assertEqual(asset.location, 'Third pump room')
            self.assertEqual(asset.useful_life_months, 12)
            self.assertEqual(Decimal(str(asset.salvage_value)), Decimal('0.00'))

    def test_fixed_asset_api_and_admin_controls(self):
        page = self.client.get('/fixed_assets')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'/static/react/assets/index.js', page.data)
        api = self.client.get('/api/portal/fixed-assets').get_json()
        self.assertIn('summary', api)
        self.assertIn('assets', api)
        self.assertIn('depreciation_entries', api)
        self.assertIn('Maintenance & Engineering', api['categories'])

        with self.client.session_transaction() as session:
            session['user'] = 'cashier'
            session['role'] = 'cashier'
        denied = self.client.post('/fixed_assets/add', data={
            'name': 'Unauthorized asset'
        })
        self.assertEqual(denied.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.FixedAsset.query.count(), 0)

    def test_recovery_receivable_settlement_aging_and_write_off(self):
        with self.app.app_context():
            department = app_module.Department(name='Phase 3B Department')
            item = app_module.InventoryItem(
                name='Phase 3B Equipment',
                current_stock=2,
                unit_cost='150.00',
                is_consumable=False
            )
            app_module.db.session.add_all([department, item])
            app_module.db.session.commit()
            department_id = department.id
            item_id = item.id

        issued = self.client.post('/assets/transfer', data={
            'item_id': item_id,
            'department_id': department_id,
            'quantity': 1,
            'borrower_name': 'Phase 3B Borrower'
        })
        self.assertEqual(issued.status_code, 302)
        with self.app.app_context():
            tracking_id = app_module.AssetTracking.query.one().id

        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        self.client.post(f'/assets/return/{tracking_id}', data={
            'condition': 'DAMAGED_LOST',
            'inspection_notes': 'Lost during use'
        })

        with self.client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'
        raised = self.client.post(f'/assets/recovery/{tracking_id}')
        self.assertEqual(raised.status_code, 302)
        with self.app.app_context():
            receivable = app_module.RecoveryReceivable.query.one()
            receivable_id = receivable.id
            receivable_reference = receivable.reference
            self.assertEqual(
                Decimal(str(receivable.amount)), Decimal('150.00')
            )

        api = self.client.get('/api/portal/recovery-receivables').get_json()
        self.assertEqual(api['summary']['open_count'], 1)
        self.assertEqual(
            Decimal(api['summary']['outstanding']), Decimal('150.00')
        )
        self.assertEqual(api['summary']['overdue_count'], 0)

        partial = self.client.post(
            f'/recovery_receivables/{receivable_id}/settle',
            data={
                'amount': '50.00',
                'settled_on': date.today().isoformat(),
                'method': 'CASH',
                'reference': 'RECEIPT-3B-1',
                'notes': 'Partial cash settlement'
            }
        )
        self.assertEqual(partial.status_code, 302)
        with self.app.app_context():
            receivable = app_module.db.session.get(
                app_module.RecoveryReceivable, receivable_id
            )
            self.assertEqual(receivable.status, 'PARTIALLY_SETTLED')
            self.assertEqual(
                Decimal(str(receivable.amount_settled)), Decimal('50.00')
            )
            self.assertEqual(receivable.balance, Decimal('100.00'))
            self.assertEqual(len(receivable.settlements), 1)

        over_settle = self.client.post(
            f'/recovery_receivables/{receivable_id}/settle',
            data={
                'amount': '150.00',
                'settled_on': date.today().isoformat(),
                'method': 'MPESA',
                'reference': 'RECEIPT-3B-OVER'
            }
        )
        self.assertEqual(over_settle.status_code, 302)
        with self.app.app_context():
            receivable = app_module.db.session.get(
                app_module.RecoveryReceivable, receivable_id
            )
            self.assertEqual(
                Decimal(str(receivable.amount_settled)), Decimal('50.00')
            )

        final = self.client.post(
            f'/recovery_receivables/{receivable_id}/settle',
            data={
                'amount': '100.00',
                'settled_on': date.today().isoformat(),
                'method': 'MPESA',
                'reference': 'RECEIPT-3B-2',
                'notes': 'Balance settled'
            }
        )
        self.assertEqual(final.status_code, 302)
        with self.app.app_context():
            receivable = app_module.db.session.get(
                app_module.RecoveryReceivable, receivable_id
            )
            self.assertEqual(receivable.status, 'SETTLED')
            self.assertEqual(receivable.balance, Decimal('0.00'))
            self.assertEqual(len(receivable.settlements), 2)

        with self.app.app_context():
            overdue = app_module.RecoveryReceivable(
                reference='RCV99001',
                borrower_name='Aging Borrower',
                department_id=department_id,
                description='Overdue test receivable',
                basis='ISSUE_COST',
                amount=Decimal('200.00'),
                amount_settled=Decimal('0.00'),
                issue_date=date.today() - timedelta(days=60),
                due_date=date.today() - timedelta(days=45),
                status='OPEN',
                created_by='admin'
            )
            app_module.db.session.add(overdue)
            app_module.db.session.commit()
            overdue_id = overdue.id

        api = self.client.get('/api/portal/recovery-receivables').get_json()
        self.assertEqual(api['summary']['open_count'], 1)
        self.assertEqual(
            Decimal(api['summary']['outstanding']), Decimal('200.00')
        )
        self.assertEqual(api['summary']['overdue_count'], 1)
        self.assertEqual(
            Decimal(api['aging']['31_60']), Decimal('200.00')
        )

        written_off = self.client.post(
            f'/recovery_receivables/{overdue_id}/write_off',
            data={'reason': 'Borrower left the club; balance unrecoverable'}
        )
        self.assertEqual(written_off.status_code, 302)
        with self.app.app_context():
            overdue = app_module.db.session.get(
                app_module.RecoveryReceivable, overdue_id
            )
            self.assertEqual(overdue.status, 'WRITTEN_OFF')
            self.assertEqual(overdue.written_off_by, 'admin')
            self.assertIn('unrecoverable', overdue.write_off_reason)
        api = self.client.get('/api/portal/recovery-receivables').get_json()
        self.assertEqual(api['summary']['open_count'], 0)
        self.assertEqual(api['summary']['written_off_count'], 1)
        self.assertEqual(
            Decimal(api['summary']['written_off_amount']), Decimal('200.00')
        )
        exported = self.client.get('/export_recovery_receivables_csv')
        self.assertEqual(exported.status_code, 200)
        csv_text = exported.get_data(as_text=True)
        self.assertIn(receivable_reference, csv_text)
        self.assertIn('RCV99001', csv_text)

    def test_legacy_recovery_expense_is_migrated_to_receivable(self):
        with self.app.app_context():
            expense = app_module.Expense(
                id='REC-LEGACY-1',
                doc_type='Recovery',
                date_created=date.today().isoformat(),
                caretaker_name='Legacy Borrower',
                item_description='Legacy damaged asset recovery',
                estimated_amount='75.00',
                actual_amount='25.00',
                status='Partially Paid',
                invoice_ref='LEGACY-REC-1',
                logged_by='admin'
            )
            app_module.db.session.add(expense)
            app_module.db.session.commit()

            app_module.init_db()
            receivable = app_module.RecoveryReceivable.query.filter_by(
                legacy_expense_id='REC-LEGACY-1'
            ).one()
            self.assertEqual(receivable.borrower_name, 'Legacy Borrower')
            self.assertEqual(
                Decimal(str(receivable.amount)), Decimal('75.00')
            )
            self.assertEqual(
                Decimal(str(receivable.amount_settled)), Decimal('25.00')
            )
            self.assertEqual(receivable.status, 'PARTIALLY_SETTLED')
            self.assertEqual(len(receivable.settlements), 1)

    def test_invoice_route_completes_bill_creation_and_receipt_flow(self):
        response = self.client.post(
            '/create_bill',
            data={
                'category': 'Temporary Guest Fee',
                'customer_name': 'Guest',
                'pax': '2'
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.startswith('/invoice/'))
        invoice = self.client.get(response.location)
        self.assertEqual(invoice.status_code, 200)
        self.assertIn(b'Guest (2 Pax)', invoice.data)
        with self.app.app_context():
            transaction = app_module.Transaction.query.one()
            self.assertEqual(transaction.amount, '400.00')
            self.assertEqual(transaction.status, 'Pending')

    def test_guest_bill_payment_supports_cash_and_m_pesa_only(self):
        with self.app.app_context():
            app_module.db.session.add(app_module.Transaction(
                id='BILL-1',
                date=f'{date.today().isoformat()} 09:00',
                customer_name='Guest',
                category='Temporary Guest Fee',
                pax=1,
                amount='200.00',
                status='Pending',
                payment_method='Unpaid',
                reference='Pending'
            ))
            app_module.db.session.commit()

        for payment_method in ('Bank Transfer', 'M-Pesa Paybill'):
            with self.subTest(payment_method=payment_method):
                rejected = self.client.post(
                    '/confirm_payment/BILL-1',
                    data={
                        'payment_method': payment_method,
                        'reference': 'BANK123456'
                    }
                )
                self.assertEqual(rejected.status_code, 302)
                with self.app.app_context():
                    self.assertEqual(
                        app_module.db.session.get(
                            app_module.Transaction, 'BILL-1'
                        ).status,
                        'Pending'
                    )

        paid = self.client.post('/confirm_payment/BILL-1', data={
            'payment_method': 'M-Pesa',
            'reference': 'MPESA12345'
        })
        self.assertEqual(paid.status_code, 302)
        with self.app.app_context():
            transaction = app_module.Transaction.query.get('BILL-1')
            self.assertEqual(transaction.status, 'Paid')
            self.assertEqual(transaction.reference, 'MPESA12345')
            self.assertEqual(transaction.payment_method, 'M-Pesa')

        with self.app.app_context():
            app_module.db.session.add(app_module.Transaction(
                id='BILL-CASH-1',
                date=f'{date.today().isoformat()} 09:00',
                customer_name='Cash Guest',
                category='Temporary Guest Fee',
                pax=1,
                amount='200.00',
                status='Pending',
                payment_method='Unpaid',
                reference='Pending'
            ))
            app_module.db.session.commit()
        cash_paid = self.client.post('/confirm_payment/BILL-CASH-1', data={
            'payment_method': 'Cash'
        })
        self.assertEqual(cash_paid.status_code, 302)
        with self.app.app_context():
            cash_transaction = app_module.db.session.get(
                app_module.Transaction, 'BILL-CASH-1'
            )
            self.assertEqual(cash_transaction.status, 'Paid')
            self.assertEqual(cash_transaction.payment_method, 'Cash')
            self.assertRegex(cash_transaction.reference, r'^CASH\d{4}$')

    def test_legacy_paybill_transactions_report_and_display_as_m_pesa(self):
        today = date.today().isoformat()
        with self.app.app_context():
            app_module.db.session.add(app_module.Transaction(
                id='LEGACY-MPESA-1',
                date=f'{today} 09:00',
                customer_name='Legacy M-Pesa Guest',
                category='Temporary Guest Fee',
                pax=1,
                amount='75.25',
                status='Paid',
                payment_method='M-Pesa Paybill',
                reference='LEGACY1234'
            ))
            app_module.db.session.commit()

        payload = self.client.get(
            '/api/portal/shift-report', query_string={'date': today}
        ).get_json()
        transaction = payload['transactions'][0]
        self.assertEqual(transaction['payment_method'], 'M-Pesa')
        self.assertEqual(payload['total_revenue'], '75.25')
        with patch('app.os.path.isfile', return_value=False), patch(
            'app.render_template', return_value='rendered'
        ) as render_template:
            response = self.client.get(
                '/shift_report', query_string={'date': today}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(render_template.call_args.kwargs['mpesa_rev'], Decimal('75.25'))

    def test_attendance_register_is_single_entry_per_date_and_validates_hours(self):
        today = date.today().isoformat()
        invalid = self.client.post('/hr/attendance', data={
            'date': today,
            'status_STF001': 'PRESENT',
            'hours_STF001': 'NaN'
        })
        self.assertEqual(invalid.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.Attendance.query.count(), 0)

        incomplete = self.client.post('/hr/attendance', data={
            'date': today,
            'end_date': today
        })
        self.assertEqual(incomplete.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.Attendance.query.count(), 0)

        marked = self.client.post('/hr/attendance', data={
            'date': today,
            'status_STF001': 'PRESENT',
            'hours_STF001': '8',
            'notes_STF001': 'Initial register'
        })
        self.assertEqual(marked.status_code, 302)
        duplicate = self.client.post('/hr/attendance', data={
            'date': today,
            'status_STF001': 'ABSENT',
            'hours_STF001': '0',
            'notes_STF001': 'Attempted overwrite'
        })
        self.assertEqual(duplicate.status_code, 302)
        with self.app.app_context():
            attendance = app_module.Attendance.query.one()
            self.assertEqual(attendance.status, 'PRESENT')
            self.assertEqual(attendance.hours_worked, 8)
            self.assertEqual(attendance.notes, 'Initial register')

    def test_attendance_register_marks_inclusive_date_range_once(self):
        app_module.init_db()
        with self.app.app_context():
            attendance_indexes = app_module.inspect(
                app_module.db.engine
            ).get_indexes('attendance')
            self.assertTrue(any(
                index['name'] == 'uq_attendance_date_staff'
                and index['unique']
                for index in attendance_indexes
            ))

        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=2)).isoformat()
        data = {
            'date': start_date,
            'end_date': end_date,
            'status_STF001': 'PRESENT',
            'hours_STF001': '8'
        }

        marked = self.client.post('/hr/attendance', data=data)
        self.assertEqual(marked.status_code, 302)
        report = self.client.get('/api/portal/hr-report', query_string={
            'start_date': start_date,
            'end_date': end_date,
            'department': 'Housekeeping'
        }).get_json()
        self.assertEqual(report['rows'][0]['days_worked'], 3)
        self.assertEqual(report['rows'][0]['total_hours'], 24)

        data['status_STF001'] = 'ABSENT'
        data['hours_STF001'] = '0'
        duplicate = self.client.post('/hr/attendance', data=data)
        self.assertEqual(duplicate.status_code, 302)
        with self.app.app_context():
            entries = app_module.Attendance.query.order_by(
                app_module.Attendance.date
            ).all()
            self.assertEqual(len(entries), 3)
            self.assertTrue(all(entry.status == 'PRESENT' for entry in entries))

    def test_first_admin_requires_safe_bootstrap_password_and_empty_db_shows_guidance(self):
        with self.app.app_context():
            app_module.db.drop_all()
            app_module.db.create_all()

        with patch.dict(os.environ, {
            'INITIAL_ADMIN_PASSWORD': '',
            'INITIAL_ADMIN_USERNAME': ''
        }):
            app_module.init_db()
        with self.app.app_context():
            self.assertEqual(app_module.User.query.count(), 0)
            self.assertGreater(app_module.Service.query.count(), 0)

        with self.client.session_transaction() as session:
            session.clear()
        login_page = self.client.get('/login')
        self.assertIn(b'No user accounts exist yet', login_page.data)
        self.assertEqual(self.client.get('/').status_code, 302)
        with patch.dict(os.environ, {
            'INITIAL_ADMIN_USERNAME': 'short-pass-admin',
            'INITIAL_ADMIN_PASSWORD': 'short'
        }):
            with self.assertRaisesRegex(RuntimeError, 'at least 8 characters'):
                app_module.init_db()

    def test_shift_and_service_analytics_use_paid_transactions(self):
        today = date.today().isoformat()
        with self.app.app_context():
            app_module.db.session.add_all([
                app_module.Transaction(
                    id='PAID-1',
                    date=f'{today} 10:00',
                    customer_name='Paid guest',
                    category='Temporary Guest Fee',
                    pax=2,
                    amount='400.00',
                    status='Paid',
                    payment_method='Cash',
                    reference='CASH1234'
                ),
                app_module.Transaction(
                    id='PENDING-1',
                    date=f'{today} 11:00',
                    customer_name='Pending guest',
                    category='Temporary Guest Fee',
                    pax=1,
                    amount='200.00',
                    status='Pending',
                    payment_method='Unpaid',
                    reference='Pending'
                ),
                app_module.Transaction(
                    id='MPESA-1',
                    date=f'{today} 12:00',
                    customer_name='M-Pesa guest',
                    category='Temporary Guest Fee',
                    pax=1,
                    amount='300.00',
                    status='Paid',
                    payment_method='M-Pesa',
                    reference='ABC1234567'
                )
            ])
            app_module.db.session.commit()

        shift = self.client.get(f'/shift_report?date={today}')
        self.assertEqual(shift.status_code, 200)
        shift_data = self.client.get(
            f'/api/portal/shift-report?date={today}'
        ).get_json()
        self.assertEqual(shift_data['total_revenue'], '700.00')
        self.assertEqual(shift_data['transactions'][0]['customer_name'], 'Paid guest')
        analytics = self.client.get(f'/analytics?date={today}')
        self.assertEqual(analytics.status_code, 200)
        analytics_data = self.client.get(
            f'/api/portal/sales-analytics?date={today}'
        ).get_json()
        transactions = analytics_data['categories']['Temporary Guest Fee']['transactions']
        self.assertEqual({row['customer_name'] for row in transactions}, {
            'Paid guest', 'Pending guest', 'M-Pesa guest'
        })
        self.assertEqual(analytics_data['categories']['Temporary Guest Fee']['quantity'], 4)
        self.assertEqual(analytics_data['mpesa_revenue'], '300.00')
        self.assertEqual(analytics_data['cash_revenue'], '400.00')
        self.assertEqual(analytics_data['total_revenue'], '700.00')
        self.assertEqual(analytics_data['paid_bill_count'], 2)
        self.assertEqual(analytics_data['pending_bill_count'], 1)
        self.assertEqual(analytics_data['pending_bill_total'], '200.00')
        self.assertEqual(analytics_data['total_bill_count'], 3)
        category_summary = analytics_data['categories']['Temporary Guest Fee']
        self.assertEqual(category_summary['paid_count'], 2)
        self.assertEqual(category_summary['pending_count'], 1)
        self.assertEqual(category_summary['pending_total'], '200.00')
        self.assertEqual(category_summary['mpesa_revenue'], '300.00')
        self.assertEqual(category_summary['cash_revenue'], '400.00')
        self.assertEqual(
            self.client.get('/shift_report?date=not-a-date').status_code,
            400
        )
        self.assertEqual(
            self.client.get('/shift_report?date=2026-1-2').status_code,
            400
        )

    def test_revenue_reports_sum_cents_exactly(self):
        today = date.today().isoformat()
        with self.app.app_context():
            app_module.db.session.add_all([
                app_module.Transaction(
                    id='CENT-001',
                    date=f'{today} 10:00',
                    customer_name='Cash Guest',
                    category='Temporary Guest Fee',
                    pax=1,
                    amount='0.10',
                    status='Paid',
                    payment_method='Cash',
                    reference='CASH1001'
                ),
                app_module.Transaction(
                    id='CENT-002',
                    date=f'{today} 10:01',
                    customer_name='M-Pesa Guest',
                    category='Temporary Guest Fee',
                    pax=1,
                    amount='0.20',
                    status='Paid',
                    payment_method='M-Pesa',
                    reference='ABC1234567'
                ),
                app_module.Transaction(
                    id='CENT-003',
                    date=f'{today} 10:02',
                    customer_name='Pending Guest',
                    category='Temporary Guest Fee',
                    pax=1,
                    amount='0.40',
                    status='Pending',
                    payment_method='Unpaid',
                    reference='Pending'
                )
            ])
            app_module.db.session.commit()

        with patch('app.os.path.isfile', return_value=False), patch(
            'app.render_template', return_value='rendered'
        ) as render_template:
            dashboard = self.client.get('/', query_string={'date': today})
            shift_report = self.client.get(
                '/shift_report', query_string={'date': today}
            )
            analytics = self.client.get(
                '/analytics', query_string={'date': today}
            )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data, b'rendered')
        self.assertEqual(shift_report.status_code, 200)
        self.assertEqual(shift_report.data, b'rendered')
        self.assertEqual(analytics.status_code, 200)
        self.assertEqual(analytics.data, b'rendered')
        self.assertEqual(
            render_template.call_args_list[0].kwargs['total_revenue'],
            Decimal('0.30')
        )
        self.assertEqual(
            render_template.call_args_list[0].kwargs['cash_revenue'],
            Decimal('0.10')
        )
        self.assertEqual(
            render_template.call_args_list[0].kwargs['mpesa_revenue'],
            Decimal('0.20')
        )
        self.assertEqual(
            render_template.call_args_list[1].kwargs['total_rev'],
            Decimal('0.30')
        )
        self.assertEqual(
            render_template.call_args_list[2].kwargs['total_revenue'],
            Decimal('0.30')
        )
        analytics_context = render_template.call_args_list[2].kwargs
        self.assertEqual(analytics_context['mpesa_revenue'], Decimal('0.20'))
        self.assertEqual(analytics_context['cash_revenue'], Decimal('0.10'))
        self.assertEqual(analytics_context['pending_bill_count'], 1)
        self.assertEqual(analytics_context['pending_bill_total'], Decimal('0.40'))
        self.assertEqual(analytics_context['total_bill_count'], 3)
        api_report = self.client.get(
            '/api/portal/shift-report', query_string={'date': today}
        ).get_json()
        self.assertEqual(api_report['total_revenue'], '0.30')

    def test_admin_page_edits_password_and_deletes_users(self):
        admin_page = self.client.get('/admin')
        self.assertEqual(admin_page.status_code, 200)

        with self.app.app_context():
            app_module.db.session.add(app_module.User(
                username='frontdesk',
                name='Front Desk',
                password_hash=app_module.generate_password_hash('old-pass-123'),
                role='cashier'
            ))
            app_module.db.session.commit()

        update = self.client.post(
            '/admin/users/frontdesk/update',
            data={
                'username': 'frontdesk2',
                'name': 'Updated Front Desk',
                'role': 'cashier'
            }
        )
        self.assertEqual(update.status_code, 302)
        password = self.client.post(
            '/admin/users/frontdesk2/password',
            data={'password': 'new-password-123'}
        )
        self.assertEqual(password.status_code, 302)
        with self.app.app_context():
            updated_user = app_module.User.query.get('frontdesk2')
            self.assertEqual(updated_user.name, 'Updated Front Desk')
            self.assertTrue(check_password_hash(
                updated_user.password_hash, 'new-password-123'
            ))

        deleted = self.client.post('/admin/users/frontdesk2/delete')
        self.assertEqual(deleted.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(app_module.User.query.get('frontdesk2'))

    def test_clear_operational_data_requires_current_admin_password(self):
        with self.app.app_context():
            app_module.db.session.add(app_module.Transaction(
                id='SALE-CLEAR-1',
                date='2026-11-04',
                customer_name='Test Guest',
                category='Guest Fee',
                amount='100.00'
            ))
            app_module.db.session.commit()

        response = self.client.post('/admin/clear-operational-data', data={
            'current_password': 'wrong-password'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.Transaction.query.count(), 1)
            self.assertIsNotNone(app_module.db.session.get(app_module.User, 'admin'))

    def test_clear_operational_data_removes_records_and_retains_settings(self):
        with self.app.app_context():
            department = app_module.Department(name='Test Department')
            item = app_module.InventoryItem(name='Test Stock Item', department=department)
            expense = app_module.Expense(
                id='LPO-CLEAR-1',
                doc_type='LPO',
                date_created='2026-11-04',
                caretaker_name='Test Buyer',
                item_description='Test purchase',
                estimated_amount='100.00'
            )
            transfer = app_module.AssetTransfer(
                id='TRF-CLEAR-1',
                date_sent='2026-11-04',
                origin='Store',
                destination='Clubhouse',
                requested_by='Test Requester',
                custodian='Test Custodian'
            )
            staff = app_module.Staff(
                id='STF-CLEAR-1',
                name='Test Staff',
                department='Housekeeping'
            )
            app_module.db.session.add_all([
                department,
                item,
                expense,
                transfer,
                staff,
                app_module.Transaction(
                    id='SALE-CLEAR-2',
                    date='2026-11-04',
                    customer_name='Test Guest',
                    category='Guest Fee',
                    amount='100.00'
                )
            ])
            app_module.db.session.flush()
            app_module.db.session.add_all([
                app_module.PaymentHistory(
                    expense_id=expense.id,
                    amount='25.00',
                    date='2026-11-04'
                ),
                app_module.TransferItem(
                    transfer_id=transfer.id,
                    name='Test Asset',
                    sent=1
                ),
                app_module.StoreRequisition(
                    item_id=item.id,
                    department_id=department.id,
                    quantity=1,
                    requester_name='Test Requester'
                ),
                app_module.AssetTracking(
                    item_id=item.id,
                    department_id=department.id,
                    borrower_name='Test Borrower',
                    quantity=1
                ),
                app_module.StockMovement(
                    item_id=item.id,
                    quantity=1,
                    movement_type='IN'
                ),
                app_module.Attendance(
                    date='2026-11-04',
                    staff_id=staff.id,
                    status='PRESENT'
                )
            ])
            app_module.db.session.commit()

        response = self.client.post('/admin/clear-operational-data', data={
            'current_password': 'admin-pass-123'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            for model in (
                app_module.Transaction,
                app_module.Expense,
                app_module.PaymentHistory,
                app_module.AssetTransfer,
                app_module.TransferItem,
                app_module.InventoryItem,
                app_module.StoreRequisition,
                app_module.AssetTracking,
                app_module.StockMovement,
                app_module.Staff,
                app_module.Attendance
            ):
                with self.subTest(model=model.__name__):
                    self.assertEqual(model.query.count(), 0)
            self.assertEqual(app_module.User.query.count(), 3)
            self.assertEqual(app_module.Service.query.count(), 1)
            self.assertEqual(app_module.Department.query.count(), 1)

    def test_clear_operational_data_removes_receipts_and_purchase_lines(self):
        with self.app.app_context():
            supplier = app_module.Supplier(name='Clear Supplier')
            expense = app_module.Expense(
                id='LPO-CLEAR-2',
                doc_type='LPO',
                date_created='2026-11-04',
                caretaker_name='Clear Buyer',
                item_description='Clear purchase',
                estimated_amount='50.00'
            )
            app_module.db.session.add_all([supplier, expense])
            app_module.db.session.flush()
            expense.supplier_id = supplier.id
            line = app_module.ExpenseLine(
                expense_id=expense.id,
                line_no=1,
                description='Clear line',
                quantity_ordered=1,
                unit_cost='50.00',
                line_total='50.00'
            )
            receipt = app_module.GoodsReceipt(
                id='GRN-CLEAR-1',
                expense_id=expense.id,
                supplier_id=supplier.id,
                delivery_note_ref='DN-CLEAR-1'
            )
            app_module.db.session.add_all([line, receipt])
            app_module.db.session.flush()
            app_module.db.session.add(app_module.GoodsReceiptLine(
                goods_receipt_id=receipt.id,
                expense_line_id=line.id,
                quantity_received=1,
                unit_cost='50.00'
            ))
            app_module.db.session.commit()

        response = self.client.post('/admin/clear-operational-data', data={
            'current_password': 'admin-pass-123'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            for model in (
                app_module.Expense,
                app_module.ExpenseLine,
                app_module.GoodsReceipt,
                app_module.GoodsReceiptLine
            ):
                with self.subTest(model=model.__name__):
                    self.assertEqual(model.query.count(), 0)
            self.assertEqual(app_module.Supplier.query.count(), 1)

    def test_clear_operational_data_removes_fixed_assets(self):
        with self.app.app_context():
            asset = app_module.FixedAsset(
                asset_tag='FA-CLEAR-1',
                name='Clear Asset',
                category='Maintenance & Engineering',
                acquisition_date=date.today(),
                acquisition_cost='100.00',
                quantity=1,
                salvage_value='0.00',
                useful_life_months=12
            )
            app_module.db.session.add(asset)
            app_module.db.session.flush()
            app_module.db.session.add(app_module.DepreciationEntry(
                fixed_asset_id=asset.id,
                period=date.today().strftime('%Y-%m'),
                amount='8.33',
                accumulated_after='8.33'
            ))
            app_module.db.session.commit()

        response = self.client.post('/admin/clear-operational-data', data={
            'current_password': 'admin-pass-123'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.FixedAsset.query.count(), 0)
            self.assertEqual(app_module.DepreciationEntry.query.count(), 0)

    def test_clear_operational_data_is_admin_only(self):
        with self.client.session_transaction() as session:
            session['role'] = 'cashier'
        response = self.client.post('/admin/clear-operational-data', data={
            'current_password': 'admin-pass-123'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(app_module.User.query.count(), 3)

    def test_service_pricing_can_be_updated_from_the_react_workflow(self):
        response = self.client.post('/manage_services', data={
            'action': 'update',
            'old_name': 'Temporary Guest Fee',
            'new_name': 'Temporary Guest Access',
            'price': '275.50'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            service = app_module.db.session.get(
                app_module.Service, 'Temporary Guest Access'
            )
            self.assertIsNotNone(service)
            self.assertEqual(service.price, 275.5)
            self.assertIsNone(app_module.db.session.get(
                app_module.Service, 'Temporary Guest Fee'
            ))

    def test_first_admin_can_be_seeded_from_environment_only_when_users_empty(self):
        with self.app.app_context():
            app_module.User.query.delete()
            app_module.db.session.commit()

        login_page = self.client.get('/login')
        self.assertIn(b'No user accounts exist yet', login_page.data)
        with patch.dict(os.environ, {
            'INITIAL_ADMIN_USERNAME': 'first-admin',
            'INITIAL_ADMIN_NAME': 'First Administrator',
            'INITIAL_ADMIN_PASSWORD': 'private-bootstrap-pass'
        }):
            app_module.init_db()

        with self.app.app_context():
            seeded = app_module.User.query.get('first-admin')
            self.assertIsNotNone(seeded)
            self.assertEqual(seeded.name, 'First Administrator')
            self.assertTrue(check_password_hash(
                seeded.password_hash, 'private-bootstrap-pass'
            ))

        with patch.dict(os.environ, {
            'INITIAL_ADMIN_USERNAME': 'first-admin',
            'INITIAL_ADMIN_PASSWORD': 'replacement-password'
        }):
            app_module.init_db()
        with self.app.app_context():
            self.assertTrue(check_password_hash(
                app_module.User.query.get('first-admin').password_hash,
                'private-bootstrap-pass'
            ))

    def test_hr_individual_ledger_and_exports_share_date_filter(self):
        first_date = (date.today() - timedelta(days=1)).isoformat()
        second_date = date.today().isoformat()
        with self.app.app_context():
            app_module.db.session.add_all([
                app_module.Attendance(
                    date=first_date,
                    staff_id='STF001',
                    status='PRESENT',
                    hours_worked=9.5,
                    is_off_day_work=True
                ),
                app_module.Attendance(
                    date=second_date,
                    staff_id='STF001',
                    status='ABSENT',
                    hours_worked=0
                )
            ])
            app_module.db.session.commit()

        params = {'start_date': first_date, 'end_date': first_date}
        ledger = self.client.get('/hr/staff/STF001', query_string=params)
        self.assertEqual(ledger.status_code, 200)
        ledger_data = self.client.get(
            '/api/portal/staff', query_string={'id': 'STF001', **params}
        ).get_json()
        self.assertEqual(ledger_data['summary']['total_hours'], 9.5)
        csv_response = self.client.get(
            '/hr/staff/STF001/export/csv', query_string=params
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response.content_type)
        rows = list(csv.reader(io.StringIO(csv_response.get_data(as_text=True))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], first_date)
        xls_response = self.client.get(
            '/hr/staff/STF001/export/xls', query_string=params
        )
        self.assertEqual(xls_response.status_code, 200)
        self.assertIn('.xls', xls_response.headers['Content-Disposition'])

        report = self.client.get(
            '/hr/report',
            query_string={
                'start_date': first_date,
                'end_date': first_date,
                'department': 'Housekeeping'
            }
        )
        self.assertEqual(report.status_code, 200)
        report_data = self.client.get(
            '/api/portal/hr-report',
            query_string={
                'start_date': first_date,
                'end_date': first_date,
                'department': 'Housekeeping'
            }
        ).get_json()
        self.assertEqual(report_data['rows'][0]['total_hours'], 9.5)
        export = self.client.get('/hr/export/csv', query_string={
            **params,
            'department': 'Housekeeping'
        })
        self.assertEqual(export.status_code, 200)
        self.assertIn(b'Amina Test', export.data)
        self.assertEqual(
            self.client.get('/hr/report', query_string={
                'start_date': second_date,
                'end_date': first_date
            }).status_code,
            400
        )

    def test_transfer_register_and_slips_render_for_sqlite_records(self):
        return_date = (date.today() + timedelta(days=1)).isoformat()
        with self.app.app_context():
            transfer = app_module.AssetTransfer(
                id='TRF1234',
                date_sent=f'{date.today().isoformat()} 08:00',
                origin='Main Store',
                destination='Poolside',
                requested_by='Events',
                approved_by='admin',
                custodian='Caretaker',
                return_date=return_date,
                status='Active'
            )
            app_module.db.session.add(transfer)
            app_module.db.session.add(app_module.TransferItem(
                transfer_id=transfer.id,
                name='Chairs',
                sent=4
            ))
            app_module.db.session.commit()

        register = self.client.get('/transfers')
        self.assertEqual(register.status_code, 200)
        transfer_data = self.client.get('/api/portal/transfers').get_json()
        self.assertEqual(transfer_data['transfers'][0]['items'][0]['name'], 'Chairs')
        dispatch = self.client.get('/transfer_slip/TRF1234')
        self.assertEqual(dispatch.status_code, 200)
        with self.client.session_transaction() as session:
            session['user'] = 'approver'
            session['role'] = 'admin'
        returned = self.client.post(
            '/return_transfer/TRF1234',
            data={
                'good_0': '3',
                'damaged_0': '1',
                'discrepancy_notes': 'One chair damaged during use'
            }
        )
        self.assertEqual(returned.status_code, 302)
        audit = self.client.get('/transfer_return_slip/TRF1234')
        self.assertEqual(audit.status_code, 200)
        with self.app.app_context():
            transfer = app_module.AssetTransfer.query.get('TRF1234')
            self.assertEqual(transfer.status, 'Discrepancy')
            self.assertEqual(app_module.TransferItem.query.one().missing, 0)

    def test_expense_and_procurement_analytics_render_with_records(self):
        with self.app.app_context():
            app_module.db.session.add(app_module.Expense(
                id='LPO1234',
                doc_type='LPO',
                date_created=f'{date.today().isoformat()} 08:00',
                caretaker_name='Caretaker',
                item_description='Cleaning supplies',
                estimated_amount='100.00',
                actual_amount='0.00',
                status='Pending Purchase',
                logged_by='admin'
            ))
            app_module.db.session.commit()

        expense_page = self.client.get('/expenses')
        self.assertEqual(expense_page.status_code, 200)
        expense_data = self.client.get('/api/portal/expenses').get_json()
        self.assertEqual(expense_data['expenses'][0]['item_description'], 'Cleaning supplies')
        self.assertEqual(self.client.get('/procurement_analytics').status_code, 200)
        self.assertEqual(self.client.get('/lpo/LPO1234').status_code, 200)
        self.assertEqual(
            self.client.get('/lpo_thermal/LPO1234').status_code, 200
        )
        self.assertEqual(
            self.client.get('/cash_voucher/LPO1234').status_code, 200
        )


if __name__ == '__main__':
    unittest.main()
