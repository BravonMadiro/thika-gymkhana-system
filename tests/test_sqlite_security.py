import csv
import io
import os
import unittest

os.environ['DATABASE_URL'] = 'sqlite://'

import app as app_module


class SQLiteSecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            app_module.db.drop_all()
            app_module.db.create_all()
            app_module.db.session.add(
                app_module.Service(name='Fee', price=100.00)
            )
            app_module.db.session.commit()
        with self.client.session_transaction() as session:
            session['user'] = 'test-admin'
            session['role'] = 'admin'

    def tearDown(self):
        with self.app.app_context():
            app_module.db.session.remove()
            app_module.db.drop_all()

    def test_bill_rejects_nonpositive_nonfinite_and_rounds_to_zero_amounts(self):
        for amount in (
            '-10', '0', 'NaN', 'Infinity', '0.001', '1000000000', '1e1000'
        ):
            with self.subTest(amount=amount):
                response = self.client.post(
                    '/create_bill',
                    data={
                        'category': 'Fee',
                        'pax': '1',
                        'customer_name': 'Test guest',
                        'custom_amount': amount,
                    },
                )
                self.assertEqual(response.status_code, 302)
                with self.app.app_context():
                    self.assertEqual(app_module.Transaction.query.count(), 0)

    def test_bill_accepts_valid_custom_amount_and_rounds_to_cents(self):
        response = self.client.post(
            '/create_bill',
            data={
                'category': 'Fee',
                'pax': '1',
                'customer_name': 'Test guest',
                'custom_amount': '12.345',
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = self.client.get(response.location)
        self.assertEqual(invoice.status_code, 200)
        with self.app.app_context():
            transaction = app_module.Transaction.query.one()
            self.assertEqual(transaction.amount, '12.35')

    def test_expense_rejects_invalid_payment_amounts_without_recording_payment(self):
        for amount in ('-5', '0', 'NaN', 'Infinity', 'not-money'):
            with self.subTest(amount=amount):
                with self.app.app_context():
                    expense = app_module.Expense(
                        id='LPO0001',
                        doc_type='LPO',
                        date_created='2026-01-01 10:00',
                        caretaker_name='Test',
                        item_description='Supplies',
                        estimated_amount='100.00',
                        actual_amount='0.00',
                        status='Pending Purchase',
                    )
                    app_module.db.session.add(expense)
                    app_module.db.session.commit()

                response = self.client.post(
                    '/fulfill_expense/LPO0001',
                    data={'actual_amount': amount},
                )

                self.assertEqual(response.status_code, 302)
                with self.app.app_context():
                    expense = app_module.Expense.query.get('LPO0001')
                    self.assertEqual(expense.status, 'Pending Purchase')
                    self.assertEqual(
                        app_module.PaymentHistory.query.filter_by(
                            expense_id='LPO0001'
                        ).count(),
                        0,
                    )
                    app_module.db.session.delete(expense)
                    app_module.db.session.commit()

    def test_expense_supports_audited_partial_payments_then_blocks_repayment(self):
        with self.app.app_context():
            app_module.db.session.add(
                app_module.Expense(
                    id='LPO0001',
                    doc_type='LPO',
                    date_created='2026-01-01 10:00',
                    caretaker_name='Test',
                    item_description='Supplies',
                    estimated_amount='100.00',
                    actual_amount='0.00',
                    status='Pending Purchase',
                )
            )
            app_module.db.session.commit()

        partial = self.client.post(
            '/fulfill_expense/LPO0001',
            data={'actual_amount': '40.00', 'invoice_ref': 'PART-1'},
        )
        self.assertEqual(partial.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.get('LPO0001')
            self.assertEqual(expense.status, 'Partially Paid')
            self.assertEqual(expense.actual_amount, '40.00')
            self.assertEqual(
                app_module.PaymentHistory.query.filter_by(
                    expense_id='LPO0001'
                ).count(),
                1,
            )

        final = self.client.post(
            '/fulfill_expense/LPO0001',
            data={'actual_amount': '60.00', 'invoice_ref': 'PART-2'},
        )
        self.assertEqual(final.status_code, 302)
        duplicate = self.client.post(
            '/fulfill_expense/LPO0001',
            data={'actual_amount': '100.00', 'invoice_ref': 'DUPLICATE'},
        )
        self.assertEqual(duplicate.status_code, 302)
        with self.app.app_context():
            expense = app_module.Expense.query.get('LPO0001')
            self.assertEqual(expense.status, 'Fulfilled')
            self.assertEqual(expense.actual_amount, '100.00')
            self.assertEqual(
                app_module.PaymentHistory.query.filter_by(
                    expense_id='LPO0001'
                ).count(),
                2,
            )

    def test_expense_payment_preserves_legacy_recorded_paid_total(self):
        with self.app.app_context():
            app_module.db.session.add(app_module.Expense(
                id='LPO0002',
                doc_type='LPO',
                date_created='2026-01-01 10:00',
                caretaker_name='Test',
                item_description='Supplies',
                estimated_amount='100.00',
                actual_amount='25.00',
                status='Partially Paid'
            ))
            app_module.db.session.commit()

        response = self.client.post('/fulfill_expense/LPO0002', data={
            'actual_amount': '10.00',
            'invoice_ref': 'LEGACY-PAY-1'
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            expense = app_module.db.session.get(app_module.Expense, 'LPO0002')
            self.assertEqual(expense.actual_amount, '35.00')
            self.assertEqual(expense.status, 'Partially Paid')
            payment = app_module.PaymentHistory.query.filter_by(
                expense_id='LPO0002'
            ).one()
            self.assertEqual(payment.amount, '10.00')

    def test_transaction_csv_neutralizes_spreadsheet_formulas(self):
        with self.app.app_context():
            app_module.db.session.add(
                app_module.Transaction(
                    id='BILL-1',
                    date='2026-01-01 10:00',
                    customer_name='=HYPERLINK("https://example.test")',
                    category='+SUM(A1:A2)',
                    pax=1,
                    amount='100.00',
                    status='Pending',
                    payment_method='Unpaid',
                    reference='Pending',
                )
            )
            app_module.db.session.commit()

        response = self.client.get('/export_csv')
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(
            rows[1][2],
            '\'=HYPERLINK("https://example.test")',
        )
        self.assertEqual(rows[1][3], "'+SUM(A1:A2)")


if __name__ == '__main__':
    unittest.main()
