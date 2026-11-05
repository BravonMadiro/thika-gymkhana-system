# Thika Gymkhana Management System

## Run the application

Install dependencies and configure an initial administrator before the first
start:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
export INITIAL_ADMIN_USERNAME=admin
read -s -p "Initial administrator password: " INITIAL_ADMIN_PASSWORD
printf '\n'
export INITIAL_ADMIN_PASSWORD
.venv/bin/python app.py
```

The initial administrator is created only when the users table is empty. The
password must be at least 8 characters, is read from the environment, and is
never assigned a default value. Keep it private. Later starts do not replace
or reset existing accounts.

On Windows PowerShell, set `$env:INITIAL_ADMIN_USERNAME = "admin"` and
`$env:INITIAL_ADMIN_PASSWORD` to a privately entered password before running
`.venv\Scripts\python.exe app.py`.

For production, also configure a persistent `FLASK_SECRET_KEY`, set
`APP_ENV=production`, and use a persistent database path via `DATABASE_URL`.

## Run tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The test modules configure an in-memory SQLite database and do not use the
club's `instance/club.db` file.

## React frontend tooling

The React frontend requires Node.js LTS and npm. Check that both are available:

```bash
node --version
npm --version
```

The interactive app now uses React for the operational overview, POS billing
and payments, inventory and requisitions, procurement, asset transfers,
administration and service pricing, attendance, and operational/HR analytics.
Flask continues to own authentication, permissions, validation, and all
business writes. Print-ready invoices, LPOs, vouchers, transfer slips, and
CSV/Excel downloads remain server-generated documents.

Install frontend packages and start the app during development in two terminals:

```bash
cd frontend
npm ci
npm run dev
```

```bash
.venv/bin/python app.py
```

Open the Vite URL shown in the first terminal. The Vite development server
proxies API calls and existing Flask workflows to port 5000. To serve the
compiled React dashboard and store screens directly from Flask:

```bash
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python app.py
```

The React store catalogue uses these parent categories and subcategories:

- Housekeeping: Cleaning Supplies, Paper & Tissue, Laundry & Linen, Waste &
  Hygiene, Other
- Food & Beverage: Bar Beverages, Kitchen Supplies, Food Ingredients, Service
  Disposables, Other
- Maintenance & Engineering: Electrical, Plumbing, Building & Carpentry, Paint
  & Hardware, Tools, Other
- Events & Banqueting: Tableware, Event Furniture, Decor, Audio-Visual
  Equipment, Other
- Sports & Recreation: Sports Equipment, Pool Supplies, Grounds & Landscaping,
  Other
- Office & Administration: Stationery, Printing, IT Accessories, Other
- Safety & Security: Personal Protective Equipment, First Aid, Fire Safety,
  Security Equipment, Other
- Furniture & Appliances: Furniture, Kitchen Appliances, Other Equipment, Other

Consumable versus durable asset remains a separate stock-control property.
Existing inventory names and categories are preserved when the new subcategory
column is added.
