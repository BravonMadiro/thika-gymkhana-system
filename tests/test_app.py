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
            '/procurement_analytics', '/hr/attendance', '/hr/staff/add',
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
            self.assertEqual(app_module.Expense.query.count(), 0)
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
            self.assertTrue(tracking.recovery_expense_id.startswith('REC'))
            recovery = app_module.db.session.get(
                app_module.Expense, tracking.recovery_expense_id
            )
            self.assertEqual(recovery.doc_type, 'Recovery')
            self.assertEqual(recovery.estimated_amount, '150.00')
            self.assertEqual(recovery.linked_asset_tracking_id, tracking_id)

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
            'Paid guest', 'Pending guest'
        })
        self.assertEqual(analytics_data['categories']['Temporary Guest Fee']['quantity'], 3)
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
