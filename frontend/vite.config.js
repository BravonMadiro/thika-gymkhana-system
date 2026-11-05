import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: projectRoot,
  base: '/static/react/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5000',
      '/login': 'http://127.0.0.1:5000',
      '/logout': 'http://127.0.0.1:5000',
      '/inventory': 'http://127.0.0.1:5000',
      '/store': 'http://127.0.0.1:5000',
      '/assets': 'http://127.0.0.1:5000',
      '/expenses': 'http://127.0.0.1:5000',
      '/transfers': 'http://127.0.0.1:5000',
      '/admin': 'http://127.0.0.1:5000',
      '/analytics': 'http://127.0.0.1:5000',
      '/hr': 'http://127.0.0.1:5000',
      '/manage_services': 'http://127.0.0.1:5000',
      '/create_expense_order': 'http://127.0.0.1:5000',
      '/create_transfer': 'http://127.0.0.1:5000',
      '/expense_payment_receipt': 'http://127.0.0.1:5000',
      '/export_csv': 'http://127.0.0.1:5000',
      '/shift_report': 'http://127.0.0.1:5000',
      '/create_bill': 'http://127.0.0.1:5000',
      '/confirm_payment': 'http://127.0.0.1:5000',
      '/invoice': 'http://127.0.0.1:5000',
      '/lpo': 'http://127.0.0.1:5000',
      '/lpo_thermal': 'http://127.0.0.1:5000',
      '/cash_voucher': 'http://127.0.0.1:5000',
      '/transfer_slip': 'http://127.0.0.1:5000',
      '/transfer_return_slip': 'http://127.0.0.1:5000',
      '/return_transfer': 'http://127.0.0.1:5000',
      '/create_recovery_bill': 'http://127.0.0.1:5000',
      '/procurement_analytics': 'http://127.0.0.1:5000',
      '/approve_expense': 'http://127.0.0.1:5000',
      '/reject_expense': 'http://127.0.0.1:5000',
      '/fulfill_expense': 'http://127.0.0.1:5000',
      '/admin/add_user': 'http://127.0.0.1:5000',
      '/admin/users': 'http://127.0.0.1:5000',
      '/delete_user': 'http://127.0.0.1:5000'
    }
  },
  build: {
    outDir: fileURLToPath(new URL('../static/react', import.meta.url)),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/index.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]'
      }
    }
  }
});
