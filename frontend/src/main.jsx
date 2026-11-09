import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, ArrowDownToLine, ArrowLeftRight, Boxes, BriefcaseBusiness,
  Check, ChevronDown, CircleDollarSign, ClipboardList, FileBarChart2,
  LayoutDashboard, LogOut, Menu, PackageCheck, Plus, Printer, Search,
  Settings, ShieldCheck, Users, X
} from 'lucide-react';
import './styles.css';

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
});

async function getJson(url) {
  const response = await fetch(url, { credentials: 'same-origin' });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(response.status === 401 ? 'Please sign in.' : 'Unable to load application data.');
    error.body = body;
    throw error;
  }
  return response.json();
}

function usePath() {
  const [path, setPath] = useState(`${window.location.pathname}${window.location.search}`);
  useEffect(() => {
    const update = () => setPath(`${window.location.pathname}${window.location.search}`);
    const handleGetForm = (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.method.toUpperCase() !== 'GET') return;
      if (form.target && form.target !== '_self') return;

      const destination = new URL(form.action || window.location.href);
      if (destination.origin !== window.location.origin) return;

      event.preventDefault();
      const query = new URLSearchParams(new FormData(form)).toString();
      const nextPath = `${destination.pathname}${query ? `?${query}` : ''}`;
      window.history.pushState({}, '', nextPath);
      setPath(nextPath);
    };
    window.addEventListener('popstate', update);
    document.addEventListener('submit', handleGetForm);
    return () => {
      window.removeEventListener('popstate', update);
      document.removeEventListener('submit', handleGetForm);
    };
  }, []);
  return [path, (next) => {
    window.history.pushState({}, '', next);
    setPath(next);
  }];
}

function App() {
  const [path, navigate] = usePath();
  const injectedIdentity = window.__TGC_IDENTITY__;
  const [identity, setIdentity] = useState(injectedIdentity ?? undefined);
  const [summary, setSummary] = useState(null);
  const [inventory, setInventory] = useState(null);
  const [portal, setPortal] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bootstrapRequired, setBootstrapRequired] = useState(Boolean(injectedIdentity?.bootstrap_required));

  const refresh = async () => {
    try {
      const state = await getJson('/api/bootstrap');
      setIdentity(state);
      setSummary(state);
      setError('');
      if (state.messages?.length) setNotice(state.messages[state.messages.length - 1].message);
    } catch (loadError) {
      setIdentity(null);
      setError(loadError.message === 'Please sign in.' ? '' : loadError.message);
      setBootstrapRequired(Boolean(loadError.body?.bootstrap_required));
    }
  };

  useEffect(() => { refresh(); }, []);

  useEffect(() => {
    const pagePath = new URL(path, window.location.origin).pathname;
    if (identity?.authenticated && (pagePath === '/inventory' || pagePath === '/')) {
      getJson('/api/inventory')
        .then(setInventory)
        .catch((loadError) => setError(loadError.message));
    }
    if (!identity?.authenticated) return;
    const portalMatch = {
      '/expenses': 'expenses',
      '/transfers': 'transfers',
      '/admin': 'admin',
      '/manage_services': 'admin',
      '/procurement_analytics': 'procurement-analytics',
      '/fixed_assets': 'fixed-assets',
      '/recovery_receivables': 'recovery-receivables',
      '/hr/attendance': 'attendance',
      '/hr/report': 'hr-report',
      '/analytics': 'sales-analytics',
      '/shift_report': 'shift-report'
    };
    let url = portalMatch[pagePath] ? `/api/portal/${portalMatch[pagePath]}` : '';
    const query = new URLSearchParams(new URL(path, window.location.origin).search);
    if (pagePath === '/hr/attendance' || pagePath === '/hr/report' || pagePath === '/expenses' || pagePath === '/analytics' || pagePath === '/shift_report') {
      if (query.size) url += `?${query.toString()}`;
    }
    const staffMatch = pagePath.match(/^\/hr\/staff\/([^/]+)$/);
    if (staffMatch) {
      url = `/api/portal/staff?id=${encodeURIComponent(staffMatch[1])}`;
      if (query.size) url += `&${query.toString()}`;
    }
    if (url) getJson(url).then(setPortal).catch((loadError) => setError(loadError.message));
  }, [identity, path]);

  useEffect(() => {
    if (!notice) return undefined;
    const timeout = window.setTimeout(() => setNotice(''), 5000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const login = async (event) => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setError('');
    try {
      const response = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(Object.fromEntries(fields.entries()))
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to sign in.');
      setIdentity(result);
      setSummary(null);
      navigate('/');
      await refresh();
    } catch (loginError) {
      setError(loginError.message);
    }
  };

  const signOut = async () => {
    await fetch('/logout', { credentials: 'same-origin' });
    setIdentity(null);
    setSummary(null);
    setInventory(null);
    setPortal(null);
    navigate('/login');
  };

  const submitForm = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const endpoint = form.getAttribute('action') || window.location.pathname;
    const data = new FormData(form);
    try {
      const response = await fetch(endpoint, {
        method: form.method?.toUpperCase() || 'POST',
        body: data,
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('The request could not be completed.');
      if (response.redirected) {
        const redirectedPath = new URL(response.url).pathname;
        if (redirectedPath.startsWith('/invoice/')) {
          window.location.assign(redirectedPath);
          return;
        }
        if (isReactPath(redirectedPath)) {
          navigate(`${redirectedPath}${new URL(response.url).search}`);
          setPortal(null);
        }
      }
      setNotice('Saved successfully.');
      await refresh();
      if (['/inventory', '/'].includes(window.location.pathname)) {
        setInventory(await getJson('/api/inventory'));
      }
      if (window.location.pathname.startsWith('/api/')) return;
      const currentPath = window.location.pathname;
      const portalPages = {
        '/expenses': 'expenses',
        '/transfers': 'transfers',
        '/admin': 'admin',
        '/manage_services': 'admin',
        '/procurement_analytics': 'procurement-analytics',
        '/hr/attendance': 'attendance',
        '/hr/report': 'hr-report',
        '/analytics': 'sales-analytics',
        '/shift_report': 'shift-report'
      };
      if (portalPages[currentPath]) {
        const query = window.location.search;
        setPortal(await getJson(`/api/portal/${portalPages[currentPath]}${query}`));
      }
      if (currentPath.startsWith('/hr/staff/') && currentPath !== '/hr/staff/add') {
        const staffId = currentPath.split('/').at(-1);
        const query = new URLSearchParams(window.location.search);
        query.set('id', staffId);
        setPortal(await getJson(`/api/portal/staff?${query}`));
      }
    } catch (formError) {
      setError(formError.message);
    }
  };

  if (identity === undefined) return <div className="loading">Loading Thika Gymkhana…</div>;
  if (!identity?.authenticated) return <Login onSubmit={login} error={error} bootstrapRequired={bootstrapRequired} />;

  const currentPath = new URL(path, window.location.origin).pathname;
  const isInventory = currentPath === '/inventory';
  const inventoryView = new URLSearchParams(new URL(path, window.location.origin).search).get('view');
  const nav = (next) => {
    navigate(next);
    setPortal(null);
    setSidebarOpen(false);
  };
  return (
    <div className="app-shell">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        active={currentPath}
        role={identity.role}
        navigate={nav}
        onLogout={signOut}
      />
      {sidebarOpen && <button className="scrim" onClick={() => setSidebarOpen(false)} aria-label="Close navigation" />}
      <main className="workspace">
        <div className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
          <div className="search-box"><Search size={17} /><span>Search records and inventory</span></div>
          <div className="topbar-user"><span>{identity.display_name || identity.user}</span><span className="avatar">{(identity.display_name || identity.user || 'U').slice(0, 1).toUpperCase()}</span></div>
        </div>
        {error && <div className="alert alert-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss"><X size={16} /></button></div>}
        {notice && <div className="alert alert-success" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss"><X size={16} /></button></div>}
        {isInventory
          ? <InventoryPage data={inventory} role={identity.role} onSubmit={submitForm} view={inventoryView} />
          : <PortalRouter path={currentPath} portal={portal} summary={summary} role={identity.role} user={identity.user} navigate={nav} onSubmit={submitForm} />}
      </main>
    </div>
  );
}

function Sidebar({ open, onClose, active, navigate, onLogout, role }) {
  const groups = [
    { title: 'OPERATIONS', description: 'Daily Club Operations', links: [
      { id: 'overview', label: 'Overview', href: '/', icon: LayoutDashboard }
    ] },
    { title: 'HR OPERATIONS', description: 'Staff & Personnel Management', links: [
      { id: '/hr/attendance', label: 'Staff Attendance', href: '/hr/attendance', icon: Users },
      { id: '/hr/staff/add', label: 'Register Staff', href: '/hr/staff/add', icon: Plus },
      { id: '/hr/report', label: 'HR Reports', href: '/hr/report', icon: FileBarChart2 }
    ] },
    { title: 'MANAGEMENT & ANALYTICS', description: 'Business Operations', links: [
      { id: '/expenses', label: 'Accounts & Procurement', href: '/expenses', icon: CircleDollarSign },
      { id: '/inventory', label: 'Store & Asset Inventory', href: '/inventory', icon: Boxes },
      { id: '/transfers', label: 'Asset Transfers', href: '/transfers', icon: ArrowLeftRight },
      { id: '/fixed_assets', label: 'Fixed Assets', href: '/fixed_assets', icon: BriefcaseBusiness },
      { id: '/recovery_receivables', label: 'Recovery Receivables', href: '/recovery_receivables', icon: CircleDollarSign },
      { id: '/procurement_analytics', label: 'Procurement Analytics', href: '/procurement_analytics', icon: FileBarChart2 },
      { id: '/analytics', label: 'Sales Analytics', href: '/analytics', icon: FileBarChart2 }
    ] },
    ...(role === 'admin' ? [{
      title: 'ADMINISTRATION',
      description: 'System & Settings',
      links: [
        { id: '/admin', label: 'User Administration', href: '/admin', icon: Users },
        { id: '/manage_services', label: 'Service Pricing', href: '/manage_services', icon: Settings }
      ]
    }] : [])
  ];
  return (
    <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
      <div className="brand"><div className="brand-mark">T</div><div><strong>TGC Ops</strong><small>Thika Gymkhana Club</small></div><ChevronDown size={16} className="brand-chevron" /></div>
      {groups.map((group) => <nav key={group.title} aria-label={group.title}>
        <div className="side-label">{group.title}<small>{group.description}</small></div>
        {group.links.map(({ id, label, href, icon: Icon }) => <a
          key={id}
          href={href}
          className={`nav-link ${active === id || (id === 'overview' && active === '/') ? 'nav-active' : ''}`}
          onClick={(event) => {
            event.preventDefault();
            navigate(href);
            onClose();
          }}
        ><Icon size={17} /><span>{label}</span></a>)}
      </nav>)}
      <div className="sidebar-bottom">
        <button className="nav-link logout-link" onClick={onLogout}><LogOut size={17} /><span>Log out</span></button>
        <div className="side-foot"><ShieldCheck size={15} /> Secure club operations</div>
      </div>
    </aside>
  );
}

function isReactPath(path) {
  return ['/', '/inventory', '/expenses', '/transfers', '/admin',
    '/manage_services', '/procurement_analytics', '/fixed_assets',
    '/recovery_receivables',
    '/hr/attendance', '/hr/staff/add', '/hr/report', '/analytics',
    '/shift_report'].includes(path) ||
    /^\/hr\/staff\/[^/]+$/.test(path);
}

function Overview({ summary, navigate, onSubmit }) {
  return (
    <section className="page-content">
      <header className="page-heading">
        <div className="club-heading"><div className="club-emblem">TGC</div><div><h1>Thika Gymkhana Club - Operational Dashboard</h1><p>{summary?.date ? new Date(`${summary.date}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }) : 'Club operations'}</p></div></div>
        <div className="header-actions"><button className="secondary-button" onClick={() => window.print()}><Printer size={16} /> Print</button><button className="secondary-button" onClick={() => window.location.href = '/export_csv'}><ArrowDownToLine size={16} /> Export</button></div>
      </header>
      <div className="metric-grid">
        <Metric icon={Users} color="red" label="Staff records" value={summary?.staff_count ?? '—'} detail="Registered staff members" />
        <Metric icon={Boxes} color="green" label="Active Store Items" value={summary?.inventory?.item_count ?? '—'} detail={`${summary?.inventory?.low_stock_count ?? 0} items at reorder level`} />
        <Metric icon={ClipboardList} color="amber" label="Pending Requisitions" value={summary?.inventory?.pending_requisitions ?? '—'} detail="Awaiting review" />
        <Metric icon={PackageCheck} color="violet" label="Assets in Use" value={summary?.inventory?.active_assets ?? '—'} detail="Currently assigned or borrowed" />
      </div>
      <section className="section-card">
        <div className="section-title"><div><span className="section-index">01</span><div><h2>Guest billing & payments</h2><p>Use current service rates and preserve each payment reference</p></div></div></div>
        <form action="/create_bill" method="post" onSubmit={onSubmit} className="billing-form">
          <label>Guest / customer name<input name="customer_name" maxLength="120" required placeholder="Guest name" /></label>
          <label>Service category<select name="category" required>{(summary?.services || []).map((service) => <option key={service.name} value={service.name}>{service.name} · {currency.format(Number(service.price))}</option>)}</select></label>
          <label>Quantity / pax<input name="pax" type="number" min="1" defaultValue="1" required /></label>
          <label>Custom amount (optional)<input name="custom_amount" type="number" min="0.01" step="0.01" placeholder="Use standard rate" /></label>
          <button className="primary-button" type="submit">Create bill <CircleDollarSign size={16} /></button>
        </form>
        <div className="table-wrap">
          <table><thead><tr><th>Bill</th><th>Guest</th><th>Service</th><th>Amount</th><th>Status</th><th>Payment</th><th>Action</th></tr></thead>
            <tbody>{(summary?.transactions || []).length ? summary.transactions.map((transaction) => <tr key={transaction.id}><td className="mono"><a href={`/invoice/${transaction.id}`}>{transaction.id}</a></td><td>{transaction.customer_name}</td><td>{transaction.category}</td><td>{currency.format(Number(transaction.amount))}</td><td><Status value={transaction.status} /></td><td>{transaction.status === 'Paid' ? `${transaction.payment_method} · ${transaction.reference}` : 'Awaiting payment'}</td><td>{transaction.status === 'Pending' ? <GuestPaymentForm transactionId={transaction.id} onSubmit={onSubmit} /> : <a className="text-button success-text" href={`/invoice/${transaction.id}`}>View invoice</a>}</td></tr>) : <EmptyRow columns={7} text="No bills have been recorded today." />}</tbody>
          </table>
        </div>
      </section>
      <section className="section-card">
        <div className="section-title"><div><span className="section-index">02</span><div><h2>Store operations</h2><p>Consumables, durable assets, and stock control</p></div></div><button className="primary-button" onClick={() => navigate('/inventory')}>Open inventory <Activity size={16} /></button></div>
        <div className="quick-grid">
          <button className="quick-card" onClick={() => navigate('/inventory?view=requisitions')}><ClipboardList size={19} /><strong>Review requisitions</strong><span>Approve, reject, and issue department requests</span></button>
          <button className="quick-card" onClick={() => navigate('/inventory?view=inventory')}><Boxes size={19} /><strong>Manage inventory</strong><span>Review levels, receipts, and physical counts</span></button>
          <a className="quick-card" href="/expenses"><BriefcaseBusiness size={19} /><strong>Procurement & accounts</strong><span>Purchase documents, approval, and payment evidence</span></a>
        </div>
      </section>
      <section className="section-card overview-note"><div className="note-icon"><ShieldCheck size={18} /></div><div><h2>Controls stay on the server</h2><p>Approvals, payments, stock changes, and role checks continue to be validated by Flask. This React dashboard uses the existing signed-in session.</p></div></section>
    </section>
  );
}

function GuestPaymentForm({ transactionId, onSubmit }) {
  const [method, setMethod] = useState('M-Pesa');
  return <form action={`/confirm_payment/${transactionId}`} method="post" onSubmit={onSubmit} className="inline-form">
    <select name="payment_method" aria-label="Payment method" value={method} onChange={(event) => setMethod(event.target.value)}>
      <option value="M-Pesa">M-Pesa</option>
      <option value="Cash">Cash</option>
    </select>
    {method === 'M-Pesa' && <input name="reference" placeholder="10-character M-Pesa ref" maxLength="10" aria-label="M-Pesa transaction reference" required />}
    <button className="text-button success-text">Confirm payment</button>
  </form>;
}

function Metric({ icon: Icon, color, label, value, detail }) {
  return <article className="metric-card"><div className={`metric-icon ${color}`}><Icon size={19} /></div><div className="metric-copy"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function PortalRouter({ path, portal, summary, role, user, navigate, onSubmit }) {
  if (path === '/') return <Overview summary={summary} navigate={navigate} onSubmit={onSubmit} />;
  if (path === '/expenses') return <ExpensesPage data={portal} role={role} user={user} navigate={navigate} onSubmit={onSubmit} />;
  if (path === '/transfers') return <TransfersPage data={portal} role={role} user={user} onSubmit={onSubmit} />;
  if (path === '/admin') return <AdminPage data={portal} user={user} onSubmit={onSubmit} />;
  if (path === '/manage_services') return <ServicesPage data={portal} onSubmit={onSubmit} />;
  if (path === '/procurement_analytics') return <ProcurementPage data={portal} navigate={navigate} />;
  if (path === '/fixed_assets') return <FixedAssetsPage data={portal} role={role} onSubmit={onSubmit} />;
  if (path === '/recovery_receivables') return <RecoveryReceivablesPage data={portal} role={role} onSubmit={onSubmit} />;
  if (path === '/hr/attendance') return <AttendancePage data={portal} navigate={navigate} onSubmit={onSubmit} />;
  if (path === '/hr/staff/add') return <AddStaffPage onSubmit={onSubmit} />;
  if (path === '/hr/report') return <HrReportPage data={portal} navigate={navigate} />;
  if (path === '/analytics') return <SalesAnalyticsPage data={portal} role={role} onSubmit={onSubmit} navigate={navigate} />;
  if (path === '/shift_report') return <ShiftReportPage data={portal} navigate={navigate} />;
  if (path.startsWith('/hr/staff/')) return <StaffLedgerPage data={portal} navigate={navigate} />;
  return <section className="page-content"><div className="section-card"><p>This screen is not available.</p></div></section>;
}

function PortalHeader({ title, description, actions }) {
  return <header className="page-heading"><div className="club-heading"><div className="club-emblem">TGC</div><div><h1>{title}</h1><p>{description}</p></div></div><div className="header-actions">{actions}</div></header>;
}

function DataTable({ headers, children, empty = 'No records found.' }) {
  const rows = React.Children.toArray(children).filter(Boolean);
  return <div className="table-wrap"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{rows.length ? rows : <EmptyRow columns={headers.length} text={empty} />}</tbody></table></div>;
}

function FormField({ label, name, type = 'text', required = false, ...props }) {
  return <label>{label}<input name={name} type={type} required={required} {...props} /></label>;
}

function ExpensesPage({ data, role, user, navigate, onSubmit }) {
  const [filter, setFilter] = useState(new URLSearchParams(window.location.search).get('type') || 'ALL');
  const [workflowFilter, setWorkflowFilter] = useState('ALL');
  const [lineRows, setLineRows] = useState([{ id: 0 }]);
  const [receiptExpenseId, setReceiptExpenseId] = useState('');
  if (!data) return <div className="loading">Loading procurement register…</div>;

  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const activeSuppliers = (data.suppliers || []).filter((supplier) => supplier.is_active);
  const workflowFilters = [
    ['ALL', 'All statuses'],
    ['Pending Approval', 'Needs approval'],
    ['Pending Purchase', 'Ready for payment'],
    ['Partially Paid', 'Partially paid'],
    ['AWAITING_RECEIPT', 'Awaiting receipt']
  ];
  const matchesWorkflow = (expense) => {
    if (workflowFilter === 'ALL') return true;
    if (workflowFilter === 'AWAITING_RECEIPT') {
      return ['PENDING', 'PARTIALLY_RECEIVED'].includes(expense.receipt_status);
    }
    return expense.status === workflowFilter;
  };
  const workflowCounts = Object.fromEntries(workflowFilters.map(([status]) => [
    status,
    data.expenses.filter((expense) => status === 'ALL' || (status === 'AWAITING_RECEIPT'
      ? ['PENDING', 'PARTIALLY_RECEIVED'].includes(expense.receipt_status)
      : expense.status === status)).length
  ]));
  const visibleExpenses = data.expenses.filter((expense) =>
    (filter === 'ALL' || expense.doc_type.toUpperCase() === filter) && matchesWorkflow(expense)
  );
  const estimatedPending = data.expenses
    .filter((expense) => ['Pending Approval', 'Pending Purchase', 'Partially Paid'].includes(expense.status))
    .reduce((sum, expense) => sum + Math.max(0, Number(expense.estimated_amount) - Number(expense.actual_amount)), 0);
  const awaitingReceipt = data.expenses.filter((expense) =>
    ['PENDING', 'PARTIALLY_RECEIVED'].includes(expense.receipt_status)
  );
  const outstandingReceiptValue = awaitingReceipt.reduce(
    (sum, expense) => sum + Number(expense.outstanding_value || 0), 0
  );
  const receiptExpense = data.expenses.find((expense) => expense.id === receiptExpenseId);
  const outstandingReceiptLines = receiptExpense
    ? receiptExpense.lines.filter((line) => line.outstanding > 0)
    : [];

  const fillLine = (event) => {
    const itemId = event.currentTarget.value;
    const row = event.currentTarget.closest('.purchase-line');
    const item = (data.inventory_items || []).find((entry) => String(entry.id) === itemId);
    if (!item || !row) return;
    const description = row.querySelector('input[name="line_description[]"]');
    const category = row.querySelector('select[name="line_category[]"]');
    const unitCost = row.querySelector('input[name="line_unit_cost[]"]');
    if (description) description.value = item.name;
    if (category) category.value = item.category;
    if (unitCost && !unitCost.value && item.unit_cost) unitCost.value = item.unit_cost;
  };

  return <section className="page-content">
    <PortalHeader title="Procurement & Recovery Register" description="Purchase documents, suppliers, receipts, matching, approvals, and payment evidence" actions={<><button className="secondary-button" onClick={() => navigate('/procurement_analytics')}><FileBarChart2 size={15} /> Analytics</button><button className="secondary-button" onClick={() => navigate('/transfers')}><ArrowLeftRight size={15} /> Asset transfers</button></>} />
    <div className="metric-grid">
      <Metric icon={ClipboardList} color="amber" label="Open documents" value={data.expenses.filter((expense) => ['Pending Approval', 'Pending Purchase', 'Partially Paid'].includes(expense.status)).length} detail={`Balance ${currency.format(estimatedPending)}`} />
      <Metric icon={CircleDollarSign} color="green" label="Spend this month" value={currency.format(Number(data.spend_month))} detail="Recorded payment history" />
      <Metric icon={CircleDollarSign} color="blue" label="Spend this year" value={currency.format(Number(data.spend_year))} detail="Recorded payment history" />
      <Metric icon={PackageCheck} color="violet" label="Awaiting receipt" value={awaitingReceipt.length} detail={`${currency.format(outstandingReceiptValue)} outstanding`} />
    </div>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">01</span><div><h2>Create a purchase document</h2><p>Record suppliers, multiple lines, and inventory items; approval and receipt are separate controls</p></div></div></div>
      <form action="/create_expense_order" method="post" onSubmit={onSubmit} className="purchase-form">
        <div className="portal-form">
          <label>Document type<select name="doc_type"><option value="LPO">Local Purchase Order</option><option value="Bill">Bill requisition</option></select></label>
          <label>Supplier<select name="supplier_id" required={activeSuppliers.length > 0}><option value="">Unassigned supplier</option>{activeSuppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
          <label>Department / cost centre<select name="department_id"><option value="">Unassigned department</option>{(data.departments || []).map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>
          <FormField label="Assigned buyer / caretaker" name="caretaker_name" maxLength="100" required />
          <button type="button" className="secondary-button" onClick={() => setLineRows([...lineRows, { id: Date.now() }])}><Plus size={15} /> Add line</button>
          <button className="primary-button" type="submit">Submit for approval <Plus size={15} /></button>
        </div>
        <div className="purchase-lines">
          {lineRows.map((row, index) => <div className="purchase-line" key={row.id}>
            <div className="portal-form">
              <label>Inventory item (optional)<select name="line_item_id[]" defaultValue="" onChange={fillLine}><option value="">Non-stock / service</option>{(data.inventory_items || []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.category}</option>)}</select></label>
              <label>Description<input name="line_description[]" maxLength="500" required placeholder={`Line ${index + 1} description`} /></label>
              <label>Category<select name="line_category[]" defaultValue="Services & Other">{(data.categories || []).map((category) => <option key={category}>{category}</option>)}</select></label>
              <label>Quantity<input name="line_quantity[]" type="number" min="1" defaultValue="1" required /></label>
              <label>Unit cost (KSh)<input name="line_unit_cost[]" type="number" min="0.01" step="0.01" required /></label>
              {lineRows.length > 1 && <button type="button" className="text-button danger-text" onClick={() => setLineRows(lineRows.filter((entry) => entry.id !== row.id))}>Remove</button>}
            </div>
          </div>)}
        </div>
      </form>
    </section>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">02</span><div><h2>Suppliers</h2><p>Supplier records drive payables and supplier-level spend reporting</p></div></div></div>
      {role === 'admin' && <form action="/suppliers/add" method="post" onSubmit={onSubmit} className="portal-form">
        <FormField label="Supplier name" name="name" maxLength="120" required />
        <FormField label="Contact person" name="contact_person" maxLength="120" />
        <FormField label="Phone" name="phone" maxLength="40" />
        <FormField label="Email" name="email" maxLength="120" />
        <FormField label="Address" name="address" maxLength="255" />
        <button className="primary-button" type="submit">Add supplier <Plus size={15} /></button>
      </form>}
      <DataTable headers={['Supplier', 'Contact', 'Phone / email', 'Status', 'Actions']}>
        {(data.suppliers || []).length ? data.suppliers.map((supplier) => <tr key={supplier.id}>
          <td><strong>{supplier.name}</strong><small>#{supplier.id}</small></td>
          <td>{supplier.contact_person || '—'}<small>{supplier.address || '—'}</small></td>
          <td>{supplier.phone || '—'}<small>{supplier.email || '—'}</small></td>
          <td><Status value={supplier.is_active ? 'ACTIVE' : 'INACTIVE'} /></td>
          <td>{role === 'admin' ? <form action={`/suppliers/${supplier.id}/toggle`} method="post" onSubmit={onSubmit}><button className="text-button">{supplier.is_active ? 'Deactivate' : 'Activate'}</button></form> : <span className="muted">—</span>}</td>
        </tr>) : <EmptyRow columns={5} text="No suppliers recorded yet." />}
      </DataTable>
    </section>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">03</span><div><h2>Purchase and recovery documents</h2><p>{visibleExpenses.length} document(s) shown · Receive goods, match invoices, and record payment</p></div></div></div>
      <div className="tabs portal-tabs" aria-label="Filter documents by status">{workflowFilters.map(([status, label]) => <button key={status} type="button" className={workflowFilter === status ? 'tab-current' : ''} aria-pressed={workflowFilter === status} onClick={() => setWorkflowFilter(status)}>{label} <span>{workflowCounts[status]}</span></button>)}</div>
      <div className="tabs portal-tabs">{[['ALL', 'All'], ['LPO', 'LPOs'], ['BILL', 'Bills']].map(([value, label]) => <button key={value} className={filter === value ? 'tab-current' : ''} onClick={() => { setFilter(value); setWorkflowFilter('ALL'); navigate(value === 'ALL' ? '/expenses' : `/expenses?type=${value}`); }}>{label} <span>{data.counts[value.toLowerCase()] ?? data.counts.all}</span></button>)}</div>
      <DataTable headers={['Reference / documents', 'Supplier / buyer', 'Description / lines', 'Ordered / received / paid', 'Receipt / match', 'Status', 'Controls']}>
        {visibleExpenses.map((expense) => <tr key={expense.id}>
          <td className="item-cell"><span className="type-pill">{expense.doc_type}</span><strong>{expense.id}</strong><small>{expense.date_created}</small><div className="row-actions"><a className="text-button" href={`/lpo/${expense.id}`} target="_blank" rel="noreferrer">A4</a><a className="text-button" href={`/lpo_thermal/${expense.id}`} target="_blank" rel="noreferrer">Thermal</a></div></td>
          <td>{expense.supplier || 'Unassigned supplier'}<small>{expense.department || 'Unassigned department'} · {expense.caretaker_name}</small><small className="rejection-note">Prepared: {expense.logged_by || '—'}{expense.approved_by && ` · Approved: ${expense.approved_by}`}</small></td>
          <td>{expense.item_description}{expense.lines?.length > 0 && <small>{expense.lines.map((line) => `${line.description} x${line.quantity_ordered}`).join(' · ')}</small>}{expense.rejection_reason && <small className="rejection-note">{expense.rejection_reason}</small>}</td>
          <td>Ordered: {currency.format(Number(expense.ordered_value || 0))}<br/>Received: {currency.format(Number(expense.received_value || 0))}<br/><strong>Paid: {currency.format(Number(expense.actual_amount))}</strong>{expense.outstanding_value && Number(expense.outstanding_value) > 0 && <small className="rejection-note">Outstanding receipt: {currency.format(Number(expense.outstanding_value))}</small>}</td>
          <td><Status value={expense.receipt_status} /><Status value={expense.match_status} />{Number(expense.price_variance || 0) !== 0 && <small className="rejection-note">Price variance: {currency.format(Number(expense.price_variance))}</small>}{expense.receipts?.length > 0 && <small>GRNs: {expense.receipts.map((receipt) => receipt.id).join(', ')}</small>}</td>
          <td><Status value={expense.status} /><div className="payment-history">{expense.payments?.map((payment) => <p key={payment.id}>{payment.date} · {currency.format(Number(payment.amount))} · {payment.paid_by || 'Unknown'}{payment.invoice_ref && ` · ${payment.invoice_ref}`}{payment.receipt_file && <> · <a href={`/expense_payment_receipt/${payment.id}`} target="_blank" rel="noreferrer">Proof</a></>}</p>)}</div></td>
          <td className="control-cell">
            {expense.status === 'Pending Approval' && role === 'admin' && expense.logged_by !== user && <><form action={`/approve_expense/${expense.id}`} method="post" onSubmit={onSubmit}><button className="text-button success-text">Approve {expense.doc_type}</button></form><form action={`/reject_expense/${expense.id}`} method="post" onSubmit={onSubmit} className="stack-form"><input name="reason" placeholder="Rejection reason" maxLength="500" required /><button className="text-button danger-text">Reject</button></form></>}
            {expense.status === 'Pending Approval' && role === 'admin' && expense.logged_by === user && <span className="muted">Prepared by you — another administrator must approve this document.</span>}
            {expense.can_receive && role === 'admin' && <button type="button" className="text-button" onClick={() => setReceiptExpenseId(expense.id)}>Receive goods</button>}
            {['Pending Purchase', 'Partially Paid'].includes(expense.status) && role === 'admin' && expense.logged_by !== user && expense.can_pay && <form action={`/fulfill_expense/${expense.id}`} method="post" encType="multipart/form-data" onSubmit={onSubmit} className="stack-form"><input name="actual_amount" type="number" min="0.01" step="0.01" placeholder="Payment / invoice amount" aria-label={`Payment amount for ${expense.id}`} required /><input name="invoice_ref" placeholder="Invoice / payment ref" aria-label={`Invoice or payment reference for ${expense.id}`} /><input name="receipt_file" type="file" accept=".pdf,.jpg,.jpeg,.png" aria-label={`Receipt file for ${expense.id}`} /><input name="service_ref_note" placeholder="Receipt or service note" aria-label={`Receipt or service note for ${expense.id}`} /><input name="variance_reason" maxLength="500" placeholder="Over-invoice / overrun reason" aria-label={`Over-invoice or overrun reason for ${expense.id}`} /><button className="text-button success-text">Record payment</button></form>}
            {['Pending Purchase', 'Partially Paid'].includes(expense.status) && role === 'admin' && expense.logged_by !== user && !expense.can_pay && <span className="muted">Receive goods before recording payment.</span>}
            {['Pending Purchase', 'Partially Paid'].includes(expense.status) && role === 'admin' && expense.logged_by === user && <span className="muted">Prepared by you — another administrator must record payment.</span>}
            {['Pending Approval', 'Pending Purchase', 'Partially Paid'].includes(expense.status) && role !== 'admin' && <span className="muted">Administrator access is required to approve, receive, or record payments.</span>}
          </td>
        </tr>)}
      </DataTable>
    </section>

    {receiptExpense && <Modal title={`Receive goods — ${receiptExpense.id}`} onClose={() => setReceiptExpenseId('')}>
      <form action={`/receive_goods/${receiptExpense.id}`} method="post" onSubmit={(event) => { onSubmit(event); setReceiptExpenseId(''); }} className="form-grid">
        <FormField label="Delivery note / GRN reference" name="delivery_note_ref" maxLength="100" required />
        <FormField label="Received date" name="received_date" type="date" defaultValue={todayIso} required />
        <label className="full">Receipt notes<input name="notes" maxLength="2000" placeholder="Optional delivery or inspection notes" /></label>
        {outstandingReceiptLines.map((line) => <div className="receipt-line" key={line.id}>
          <strong>{line.description}</strong>
          <small>Ordered {line.quantity_ordered} · Received {line.quantity_received} · Rejected {line.quantity_rejected} · Outstanding {line.outstanding}</small>
          <input type="hidden" name="line_id[]" value={line.id} />
          <label>Accepted quantity<input name="quantity_received[]" type="number" min="0" max={line.outstanding} defaultValue={line.outstanding} required /></label>
          <label>Rejected quantity<input name="quantity_rejected[]" type="number" min="0" max={line.outstanding} defaultValue="0" required /></label>
          <label>Unit cost (KSh)<input name="unit_cost[]" type="number" min="0.01" step="0.01" defaultValue={line.unit_cost} required /></label>
          <label>Price variance reason<input name="variance_reason[]" maxLength="500" placeholder="Required if unit cost differs" /></label>
        </div>)}
        <ModalActions onCancel={() => setReceiptExpenseId('')} />
      </form>
    </Modal>}
  </section>;
}


function AdminPage({ data, user, onSubmit }) {
  if (!data) return <div className="loading">Loading users…</div>;
  return <section className="page-content"><PortalHeader title="Administration" description="Manage staff access and club accounts" actions={<a className="secondary-button" href="/manage_services">Manage service pricing</a>}/>
    <section className="section-card"><div className="section-title"><div><span className="section-index">01</span><div><h2>Create user account</h2><p>Account roles control access to sensitive actions</p></div></div></div><form action="/admin/add_user" method="post" onSubmit={onSubmit} className="portal-form"><FormField label="Username" name="username" maxLength="64" required/><FormField label="Full name" name="name" maxLength="120" required/><FormField label="Password" name="password" type="password" required/><label>Role<select name="role"><option value="cashier">Cashier / Front Desk</option><option value="admin">Administrator</option></select></label><button className="primary-button">Create user <Plus size={15}/></button></form></section>
    <section className="section-card"><div className="section-title"><div><span className="section-index">02</span><div><h2>System users</h2><p>{data.users.length} active account(s)</p></div></div></div><div className="user-grid">{data.users.map((account)=><article className="user-card" key={account.username}><header><div className="avatar">{account.name.slice(0,1).toUpperCase()}</div><div><strong>{account.name}</strong><small>{account.username} · {account.role}</small></div></header><form action={`/admin/users/${encodeURIComponent(account.username)}/update`} method="post" onSubmit={onSubmit} className="stack-form"><input name="username" defaultValue={account.username} maxLength="64" required/><input name="name" defaultValue={account.name} maxLength="120" required/><select name="role" defaultValue={account.role}><option value="cashier">Cashier</option><option value="admin">Administrator</option></select><button className="secondary-button">Save details</button></form><form action={`/admin/users/${encodeURIComponent(account.username)}/password`} method="post" onSubmit={onSubmit} className="row-actions"><input name="password" type="password" minLength="8" placeholder="New password" required/><button className="text-button success-text">Change password</button></form><form action={`/admin/users/${encodeURIComponent(account.username)}/delete`} method="post" onSubmit={(event)=>{if(account.username===user){event.preventDefault();return;} if(!window.confirm(`Delete ${account.username}?`)) event.preventDefault(); else onSubmit(event);}}><button className="text-button danger-text" disabled={account.username===user}>Delete {account.username===user?'(current user)':'user'}</button></form></article>)}</div></section>
    <section className="section-card danger-card"><div className="section-title"><div><span className="section-index">!</span><div><h2>Clear operational data</h2><p>This permanently removes sales, procurement documents and receipts, inventory, fixed-asset records, recovery receivables, asset-transfer, attendance, and staff records.</p></div></div></div><div className="danger-content"><p><strong>Retained:</strong> administrator and other user accounts, service pricing, supplier master data, and department settings.</p><form action="/admin/clear-operational-data" method="post" onSubmit={(event)=>{if(!window.confirm('Permanently clear all operational records? User accounts, service pricing, suppliers, and departments will be retained. This cannot be undone.')){event.preventDefault();return;} onSubmit(event);}} className="portal-form"><FormField label="Your current administrator password" name="current_password" type="password" autoComplete="current-password" required/><button className="danger-button" type="submit">Clear operational data</button></form></div></section>
  </section>;
}

function ServicesPage({ data, onSubmit }) {
  if (!data) return <div className="loading">Loading service rates…</div>;
  return <section className="page-content"><PortalHeader title="Service pricing" description="Manage billing categories and unit rates; existing bills retain their recorded amounts." actions={<a className="secondary-button" href="/">Back to overview</a>}/>
    <section className="section-card"><div className="section-title"><div><span className="section-index">01</span><div><h2>Add service</h2><p>New services become available in the guest billing form</p></div></div></div><form action="/manage_services" method="post" onSubmit={onSubmit} className="portal-form"><input type="hidden" name="action" value="add"/><FormField label="Service name" name="service_name" maxLength="100" required/><FormField label="Price per unit (KSh)" name="price" type="number" min="0.01" step="0.01" required/><button className="primary-button"><Plus size={15}/> Add service</button></form></section>
    <section className="section-card"><div className="section-title"><div><span className="section-index">02</span><div><h2>Active service rates</h2></div></div></div><div className="service-list">{data.services.map((service)=><div className="service-row" key={service.name}><form action="/manage_services" method="post" onSubmit={onSubmit}><input type="hidden" name="action" value="update"/><input type="hidden" name="old_name" value={service.name}/><input name="new_name" defaultValue={service.name} maxLength="100" required/><input name="price" type="number" min="0" step="0.01" defaultValue={service.price} required/><button className="secondary-button">Save</button></form><form action="/manage_services" method="post" onSubmit={(event)=>{if(!window.confirm(`Remove ${service.name} from future billing choices?`)) event.preventDefault(); else onSubmit(event);}}><input type="hidden" name="action" value="delete"/><input type="hidden" name="service_name" value={service.name}/><button className="text-button danger-text">Delete</button></form></div>)}</div></section>
  </section>;
}

function TransfersPage({ data, role, user, onSubmit }) {
  const [rows, setRows] = useState([{id:0}]);
  if (!data) return <div className="loading">Loading asset transfer register…</div>;
  return <section className="page-content"><PortalHeader title="Asset transfers & reconciliation" description="Inter-department dispatch, return inspection, and discrepancy recovery" actions={<a href="/expenses" className="secondary-button">Procurement & recovery</a>}/>
    <div className="metric-grid"><Metric icon={ArrowLeftRight} color="blue" label="Active transfers" value={data.active_count} detail="Currently in transit / assigned"/><Metric icon={Activity} color="red" label="Overdue / discrepancies" value={data.overdue_count+data.discrepancy_count} detail="Requires follow-up"/><Metric icon={ClipboardList} color="green" label="Tracked movements" value={data.transfers.length} detail="All recorded dispatches"/><Metric icon={CircleDollarSign} color="amber" label="Recovery cases" value={data.transfers.filter((t)=>t.recovery_bill_id||t.recovery_receivable_id).length} detail="Recovery receivables linked"/></div>
    <section className="section-card"><div className="section-title"><div><span className="section-index">01</span><div><h2>Log a multi-item transfer</h2><p>Record custody and expected return date before dispatch</p></div></div></div>{role==='admin'?<form action="/create_transfer" method="post" onSubmit={onSubmit} className="transfer-form"><div className="transfer-items"><div className="section-title"><strong>Items</strong><button type="button" className="secondary-button" onClick={()=>setRows([...rows,{id:Date.now()}])}><Plus size={14}/> Add item</button></div>{rows.map((row,index)=><div className="portal-form transfer-item" key={row.id}><label>Durable item<select name="item_id[]" required defaultValue=""><option value="">Select durable item</option>{data.inventory_items.map((item)=><option key={item.id} value={item.id}>{item.name} ({item.current_stock} {item.unit_of_measure} available)</option>)}</select></label><FormField label="Quantity" name="qty_sent[]" type="number" min="1" defaultValue="1" required/>{rows.length>1&&<button type="button" className="text-button danger-text" onClick={()=>setRows(rows.filter((item)=>item.id!==row.id))}>Remove</button>}</div>)}</div><div className="portal-form"><FormField label="Origin" name="origin" defaultValue="Main Store" required/><FormField label="Destination" name="destination" required/><label>Destination department<select name="department_id" required><option value="">Select department</option>{data.departments.map((dept)=><option key={dept.id} value={dept.id}>{dept.name}</option>)}</select></label><FormField label="Requested by" name="requested_by" required/><FormField label="Custodian / transporter" name="custodian" required/><FormField label="Expected return date" name="return_date" type="date" required/><button className="primary-button">Generate dispatch slip</button></div></form>:<p className="muted">An administrator must authorize dispatches.</p>}</section>
    <section className="section-card"><div className="section-title"><div><span className="section-index">02</span><div><h2>Transfer register & return reconciliation</h2><p>For return, count good and damaged quantities per dispatched item</p></div></div></div><DataTable headers={['Reference / slips','Items & route','Accountability','Status / due','Return inspection']}>{data.transfers.map((transfer)=><tr key={transfer.id}><td className="item-cell"><strong>{transfer.id}</strong><small>{transfer.date_sent}</small><div className="row-actions"><a className="text-button" href={`/transfer_slip/${transfer.id}`} target="_blank" rel="noreferrer">Dispatch</a>{['Fully Returned','Discrepancy','Resolved & Paid'].includes(transfer.status)&&<a className="text-button" href={`/transfer_return_slip/${transfer.id}`} target="_blank" rel="noreferrer">Return slip</a>}</div></td><td>{transfer.items.map((item)=><div key={item.name}><strong>{item.name}</strong> · sent {item.sent}, good {item.returned_good}, damaged {item.damaged}, missing {item.missing}</div>)}<small>{transfer.origin} → {transfer.destination}</small></td><td>Requester: {transfer.requested_by}<br/>Custodian: {transfer.custodian}<br/>Authorized: {transfer.approved_by||'—'}<br/>Reconciled: {transfer.reconciled_by||'—'}</td><td><Status value={transfer.status}/><small className={transfer.is_overdue?'rejection-note':''}>Due {transfer.return_date}{transfer.is_overdue?' · OVERDUE':''}</small>{transfer.discrepancy_notes&&transfer.discrepancy_notes!=='N/A'&&<small className="rejection-note">{transfer.discrepancy_notes}</small>}</td><td>{['Fully Returned','Resolved & Paid'].includes(transfer.status)?<>Reconciled{transfer.recovery_receivable_id&&<small>Receivable: {transfer.recovery_receivable_id}</small>}{transfer.recovery_bill_id&&<small>Legacy recovery: {transfer.recovery_bill_id}</small>}</>:role==='admin'&&transfer.approved_by!==user&&transfer.reconciled_by!==user?<form action={`/return_transfer/${transfer.id}`} method="post" onSubmit={onSubmit} className="stack-form">{transfer.items.map((item,index)=><div key={item.name}><strong>{item.name} (sent {item.sent})</strong><input name={`good_${index}`} type="number" min="0" max={item.sent} defaultValue={item.returned_good} placeholder="Good"/><input name={`damaged_${index}`} type="number" min="0" max={item.sent} defaultValue={item.damaged} placeholder="Damaged"/></div>)}<input name="discrepancy_notes" placeholder="Explain discrepancy"/><button className="secondary-button">Record return</button></form>:<span className="muted">Different administrator required</span>}{transfer.status==='Discrepancy'&&!transfer.recovery_bill_id&&!transfer.recovery_receivable_id&&role==='admin'&&transfer.reconciled_by!==user&&<form action={`/create_recovery_bill/${transfer.id}`} method="post" onSubmit={onSubmit} className="stack-form">{transfer.items.filter((item)=>item.damaged||item.missing).map((item)=><div key={item.name}><strong>{item.name}</strong> · {item.damaged+item.missing} affected · {item.recovery_value ? currency.format(Number(item.recovery_value)) : 'cost missing'}</div>)}<button className="text-button danger-text">Raise recovery receivable</button></form>}</td></tr>)}</DataTable></section>
  </section>;
}

function AttendancePage({ data, navigate, onSubmit }) {
  if (!data) return <div className="loading">Loading attendance register…</div>;
  return <section className="page-content"><PortalHeader title="Daily staff attendance" description="Record one immutable attendance register per date" actions={<><button className="secondary-button" onClick={()=>navigate('/hr/staff/add')}><Plus size={15}/> Add staff</button><button className="secondary-button" onClick={()=>navigate('/hr/report')}>Payroll report</button></>}/>
    <section className="section-card"><form method="get" action="/hr/attendance" className="portal-form"><label>From date<input type="date" name="date" max={data.max_date} defaultValue={data.date}/></label><label>Through date<input type="date" name="end_date" min={data.date} max={data.max_date} defaultValue={data.end_date||data.date}/></label><button className="secondary-button">Load dates</button></form><div className="section-title"><div><span className="section-index">01</span><div><h2>Staff register · {data.date}{data.end_date!==data.date?` through ${data.end_date}`:''}</h2><p>{data.staff.length} employee(s)</p></div></div>{data.attendance_marked&&<Status value="SAVED"/>}</div><form method="post" action="/hr/attendance" onSubmit={onSubmit}><input type="hidden" name="date" value={data.date}/><input type="hidden" name="end_date" value={data.end_date||data.date}/><DataTable headers={['Staff','Department','Status','Hours','Off day','Holiday','Notes']}>{data.staff.map((staff)=><tr key={staff.id}><td><button type="button" className="text-button" onClick={()=>navigate(`/hr/staff/${staff.id}`)}>{staff.name}</button><small>{staff.id}</small></td><td>{staff.department}</td><td><select name={`status_${staff.id}`} defaultValue={staff.attendance?.status||'PRESENT'} disabled={data.attendance_marked}><option>PRESENT</option><option>ABSENT</option><option>LATE</option><option>LEAVE</option></select></td><td><input className="small-input" type="number" name={`hours_${staff.id}`} min="0" max="24" step="0.25" defaultValue={staff.attendance?.hours_worked??8} disabled={data.attendance_marked}/></td><td><input type="checkbox" name={`off_day_${staff.id}`} defaultChecked={staff.attendance?.is_off_day_work} disabled={data.attendance_marked}/></td><td><input type="checkbox" name={`holiday_${staff.id}`} defaultChecked={staff.attendance?.is_holiday_work} disabled={data.attendance_marked}/></td><td><input className="notes-input" name={`notes_${staff.id}`} defaultValue={staff.attendance?.notes} disabled={data.attendance_marked}/></td></tr>)}</DataTable><div className="section-title"><span className="muted">{data.attendance_marked?'At least one date in this range is already saved.':'Selected statuses and hours will be used for each date in the range.'}</span><button className="primary-button" disabled={!data.staff.length||data.attendance_marked}>Save attendance</button></div></form></section>
  </section>;
}

function AddStaffPage({ onSubmit }) {
  return <section className="page-content"><PortalHeader title="Register staff member" description="Add a staff record for attendance and HR reporting" actions={<a href="/hr/attendance" className="secondary-button">Back to attendance</a>}/><section className="section-card"><form action="/hr/staff/add" method="post" onSubmit={onSubmit} className="portal-form"><FormField label="Full name" name="name" maxLength="120" required/><FormField label="Department" name="department" maxLength="120" required/><button className="primary-button">Add employee</button></form></section></section>;
}

function HrReportPage({ data, navigate }) {
  const params = new URLSearchParams(window.location.search);
  if (!data) return <div className="loading">Loading HR report…</div>;
  return <section className="page-content"><PortalHeader title="Attendance & payroll report" description="Filter by date and department; export detailed attendance summaries" actions={<button className="secondary-button" onClick={()=>navigate('/hr/attendance')}>Daily attendance</button>}/><section className="section-card"><form method="get" action="/hr/report" className="portal-form"><FormField label="Start date" name="start_date" type="date" defaultValue={data.start_date} required/><FormField label="End date" name="end_date" type="date" defaultValue={data.end_date} required/><label>Department<select name="department" defaultValue={data.department}><option value="ALL">All departments</option>{data.departments.map((dept)=><option key={dept}>{dept}</option>)}</select></label><button className="primary-button">Generate report</button></form>{data.start_date&&data.end_date&&<><div className="section-title"><h2>Attendance {data.start_date} to {data.end_date}</h2><div className="row-actions"><a className="secondary-button" href={`/hr/export/csv?${params}`}>Download CSV</a><a className="secondary-button" href={`/hr/export/excel?${params}`}>Download Excel</a></div></div><DataTable headers={['Staff','Department','Days worked','Off days','Holidays','Hours','Overtime','Undertime']}>{data.rows.map((row)=><tr key={row.staff.id}><td><button className="text-button" onClick={()=>navigate(`/hr/staff/${row.staff.id}`)}>{row.staff.name}</button></td><td>{row.staff.department}</td><td>{row.days_worked}</td><td>{row.off_day_work}</td><td>{row.holiday_work}</td><td>{Number(row.total_hours).toFixed(2)}</td><td>{Number(row.overtime_hours).toFixed(2)}</td><td>{Number(row.undertime_hours).toFixed(2)}</td></tr>)}</DataTable></>}</section></section>;
}

function StaffLedgerPage({ data, navigate }) {
  if (!data) return <div className="loading">Loading staff ledger…</div>;
  const query = new URLSearchParams(window.location.search);
  return <section className="page-content"><PortalHeader title={data.staff.name} description={`${data.staff.department} · ${data.staff.id}`} actions={<button className="secondary-button" onClick={()=>navigate('/hr/attendance')}>Back to attendance</button>}/><section className="section-card"><form method="get" className="portal-form"><FormField label="From date" name="start_date" type="date" defaultValue={data.start_date}/><FormField label="To date" name="end_date" type="date" defaultValue={data.end_date}/><button className="primary-button">Filter ledger</button><a className="secondary-button" href={`/hr/staff/${data.staff.id}/export/csv?${query}`}>CSV</a><a className="secondary-button" href={`/hr/staff/${data.staff.id}/export/xls?${query}`}>Excel</a></form><div className="metric-grid"><Metric icon={Activity} color="blue" label="Days worked" value={data.summary.total_days} detail="In selected period"/><Metric icon={ClipboardList} color="green" label="Total hours" value={Number(data.summary.total_hours).toFixed(2)} detail="Recorded attendance"/><Metric icon={Activity} color="amber" label="Off-day shifts" value={data.summary.off_day_shifts} detail="Flagged workdays"/><Metric icon={Activity} color="violet" label="Holiday shifts" value={data.summary.holiday_shifts} detail="Flagged holidays"/></div><DataTable headers={['Date','Status','Hours','Off-day','Holiday','Notes']}>{data.history.map((entry)=><tr key={entry.date}><td>{entry.date}</td><td><Status value={entry.status}/></td><td>{entry.hours_worked}</td><td>{entry.is_off_day_work?'Yes':'—'}</td><td>{entry.is_holiday_work?'Yes':'—'}</td><td>{entry.notes||'—'}</td></tr>)}</DataTable></section></section>;
}

function ProcurementPage({ data, navigate }) {
  const [search, setSearch] = useState('');
  if (!data) return <div className="loading">Loading procurement analytics…</div>;
  const records = (data.records || []).filter((row) => `${row.id} ${row.item} ${row.audit_ref} ${row.invoice_ref}`.toLowerCase().includes(search.toLowerCase()));
  const spendTable = (title, index, description, rows) => <section className="section-card">
    <div className="section-title"><div><span className="section-index">{index}</span><div><h2>{title}</h2><p>{description}</p></div></div></div>
    <DataTable headers={['Name', 'Recorded spend']}>
      {(rows || []).length ? rows.map((row) => <tr key={row.name}><td>{row.name}</td><td>{currency.format(Number(row.amount))}</td></tr>) : <EmptyRow columns={2} text="No payment history yet." />}
    </DataTable>
  </section>;
  return <section className="page-content">
    <PortalHeader title="Procurement & financial analytics" description="Commitments, goods received, supplier/department spend, price variance, and payment matching" actions={<button className="secondary-button" onClick={() => navigate('/expenses')}>Back to procurement</button>} />
    <div className="metric-grid">
      <Metric icon={CircleDollarSign} color="blue" label="Committed LPO spend" value={currency.format(Number(data.committed))} detail="Approved purchase orders" />
      <Metric icon={PackageCheck} color="green" label="Goods received" value={currency.format(Number(data.received_value_total || 0))} detail="Accepted GRN value" />
      <Metric icon={Activity} color="amber" label="Price variance" value={currency.format(Number(data.price_variance_total || 0))} detail="Received cost vs ordered cost" />
      <Metric icon={ClipboardList} color="red" label="Outstanding receipt" value={currency.format(Number(data.outstanding_receipt_value || 0))} detail="Ordered but not yet received" />
    </div>
    <div className="metric-grid">{Object.entries(data.status_counts).map(([label, count]) => <Metric key={label} icon={ClipboardList} color="violet" label={label} value={count} detail="Documents" />)}</div>
    {spendTable('Spend by supplier', '01', 'Payments grouped by supplier master record', data.spend_by_supplier)}
    {spendTable('Spend by department / cost centre', '02', 'Payments grouped by the department recorded on the purchase document', data.spend_by_department)}
    {spendTable('Spend by procurement category', '03', 'Payments allocated across purchase lines by ordered value', data.spend_by_category)}
    <section className="section-card">
      <div className="section-title"><div><span className="section-index">04</span><div><h2>Receipt and match status</h2><p>Three-way control across ordered, received, and paid values</p></div></div></div>
      <DataTable headers={['Bucket', 'Documents']}>
        {Object.entries(data.receipt_status_counts || {}).map(([label, count]) => <tr key={`receipt-${label}`}><td>Receipt: {label}</td><td>{count}</td></tr>)}
        {Object.entries(data.match_status_counts || {}).map(([label, count]) => <tr key={`match-${label}`}><td>Match: {label}</td><td>{count}</td></tr>)}
      </DataTable>
    </section>
    <section className="section-card">
      <div className="section-title"><div><span className="section-index">05</span><div><h2>Receipt price variance</h2><p>Lines received at a different cost from the approved LPO</p></div></div></div>
      <DataTable headers={['Document', 'Date', 'Supplier', 'Price variance']}>
        {(data.price_variance_records || []).length ? data.price_variance_records.map((record) => <tr key={record.id}><td>{record.id}</td><td>{record.date}</td><td>{record.supplier}</td><td>{currency.format(Number(record.amount))}</td></tr>) : <EmptyRow columns={4} text="No receipt price variance recorded." />}
      </DataTable>
    </section>
    {spendTable('Spend by buyer / caretaker', '06', 'Recorded payments grouped by the buyer responsible for the purchase', data.cost_centers)}
    <section className="section-card">
      <div className="section-title"><div><span className="section-index">07</span><div><h2>Variance audit log</h2><p>Search estimates and their recorded payment differences</p></div></div><label className="table-search"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search reference or description" /></label></div>
      <DataTable headers={['ID / date', 'Description', 'Audit / invoice ref', 'Variance type', 'Amount']}>
        {records.length ? records.map((record) => <tr key={record.id}><td>{record.id}<small>{record.date}</small></td><td>{record.item}</td><td>{record.audit_ref || '—'} / {record.invoice_ref}</td><td>{record.variance_type}</td><td>{currency.format(Number(record.variance_amount))}</td></tr>) : <EmptyRow columns={5} text="No payment variance records." />}
      </DataTable>
    </section>
  </section>;
}

function LegacyBillImportForm({ services, onSubmit }) {
  return <section className="section-card">
    <div className="section-title"><div><span className="section-index">📥</span><div><h2>Import pre-system legacy bill</h2><p>Record a historical paid bill against its original business date</p></div></div></div>
    <form action="/admin/import_legacy_bill" method="post" onSubmit={onSubmit} className="portal-form legacy-import-form">
      <FormField label="Customer / member name" name="customer_name" maxLength="120" required placeholder="e.g. John Doe" />
      <label>Category / service<select name="category" required>{(services || []).map((service) => <option key={service}>{service}</option>)}</select></label>
      <FormField label="Historical date" name="historical_date" type="date" required />
      <FormField label="Amount (KES)" name="amount" type="number" min="0.01" max="999999999.99" step="0.01" required placeholder="0.00" />
      <label>Payment method<select name="payment_method" defaultValue="Cash"><option value="Cash">Cash</option><option value="M-Pesa">M-Pesa</option></select></label>
      <FormField label="Legacy reference / notes" name="reference" maxLength="50" defaultValue="LEGACY-RECORD" />
      <div className="legacy-form-actions"><button className="primary-button" type="submit">Import historical bill</button></div>
    </form>
  </section>;
}

function SalesAnalyticsPage({ data, role, onSubmit }) {
  if (!data) return <div className="loading">Loading sales analytics…</div>;
  const params = new URLSearchParams(window.location.search);
  return <section className="page-content">
    <PortalHeader title="Sales & service analytics" description="Daily collections, payment methods, and paid versus pending bills" actions={<a className="secondary-button" href={`/shift_report?date=${data.date}`} target="_blank" rel="noreferrer">Print shift report</a>} />
    <section className="section-card">
      <form method="get" className="portal-form">
        <FormField label="Business date" name="date" type="date" defaultValue={data.date} />
        <label>Service category<select name="service_search" defaultValue={params.get('service_search') || ''}><option value="">All categories</option>{(data.services || []).map((service) => <option key={service}>{service}</option>)}</select></label>
        <button className="primary-button">Apply filters</button>
      </form>
      <div className="metric-grid">
        <Metric icon={CircleDollarSign} color="blue" label="M-Pesa collected" value={currency.format(Number(data.mpesa_revenue))} detail="Paid bills only" />
        <Metric icon={CircleDollarSign} color="amber" label="Cash collected" value={currency.format(Number(data.cash_revenue))} detail="Paid bills only" />
        <Metric icon={CircleDollarSign} color="green" label="Total collected" value={currency.format(Number(data.total_revenue))} detail={`${data.paid_bill_count} paid bill(s)`} />
        <Metric icon={ClipboardList} color="green" label="Paid bills" value={data.paid_bill_count} detail={currency.format(Number(data.total_revenue))} />
        <Metric icon={ClipboardList} color="red" label="Pending bills" value={data.pending_bill_count} detail={currency.format(Number(data.pending_bill_total))} />
        <Metric icon={ClipboardList} color="violet" label="Total bills" value={data.total_bill_count} detail="Paid and pending" />
      </div>
      <DataTable headers={['Category', 'Bills', 'Pax / quantity', 'Paid bills', 'Pending bills', 'M-Pesa', 'Cash', 'Total collected', 'Bill details']}>
        {Object.entries(data.categories || {}).map(([category, summary]) => <tr key={category}>
          <td>{category}</td>
          <td>{summary.count}</td>
          <td>{summary.quantity}</td>
          <td>{summary.paid_count}</td>
          <td>{summary.pending_count} · {currency.format(Number(summary.pending_total))}</td>
          <td>{currency.format(Number(summary.mpesa_revenue))}</td>
          <td>{currency.format(Number(summary.cash_revenue))}</td>
          <td>{currency.format(Number(summary.revenue))}</td>
          <td>{(summary.transactions || []).map((transaction) => <div key={transaction.id}>{transaction.id} · {transaction.customer_name} · {transaction.status} · {currency.format(Number(transaction.amount))}</div>)}</td>
        </tr>)}
      </DataTable>
    </section>
    {role === 'admin' && <LegacyBillImportForm services={data.services || []} onSubmit={onSubmit} />}
  </section>;
}

function ShiftReportPage({ data }) {
  if (!data) return <div className="loading">Loading shift report…</div>;
  return <section className="page-content">
    <PortalHeader title="Shift collection report" description={`Paid transactions for ${data.date}`} actions={<button className="secondary-button" onClick={() => window.print()}><Printer size={15} /> Print</button>} />
    <section className="section-card">
      <form method="get" className="portal-form"><FormField label="Business date" name="date" type="date" defaultValue={data.date} /><button className="primary-button">Load report</button></form>
      <div className="metric-grid">
        <Metric icon={CircleDollarSign} color="green" label="Total collected" value={currency.format(Number(data.total_revenue))} detail="Paid bills only" />
        <Metric icon={ClipboardList} color="blue" label="Paid bills" value={data.total_transactions} detail="Included in this report" />
      </div>
      <DataTable headers={['Bill ID', 'Date / time', 'Customer', 'Service', 'Pax', 'Amount', 'Payment method', 'Reference']}>
        {(data.transactions || []).map((transaction) => <tr key={transaction.id}><td>{transaction.id}</td><td>{transaction.date}</td><td>{transaction.customer_name}</td><td>{transaction.category}</td><td>{transaction.pax}</td><td>{currency.format(Number(transaction.amount))}</td><td>{transaction.payment_method}</td><td>{transaction.reference}</td></tr>)}
      </DataTable>
      <div className="section-title"><strong>Total: {currency.format(Number(data.total_revenue))}</strong><a className="secondary-button" href={`/export_csv?date=${data.date}`}>Export transactions</a></div>
    </section>
  </section>;
}

function RecoveryReceivablesPage({ data, role, onSubmit }) {
  const [settleId, setSettleId] = useState('');
  const [writeOffId, setWriteOffId] = useState('');
  if (!data) return <div className="loading">Loading recovery receivables…</div>;

  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const receivables = data.receivables || [];
  const methods = data.methods || ['CASH'];
  const settleReceivable = receivables.find((row) => row.id === settleId);
  const writeOffReceivable = receivables.find((row) => row.id === writeOffId);
  const activeStatuses = ['OPEN', 'PARTIALLY_SETTLED'];
  const aging = data.aging || {};

  return <section className="page-content">
    <PortalHeader title="Recovery receivables" description="Damage/loss recharges, settlements, write-offs, and overdue aging" actions={<a className="secondary-button" href="/export_recovery_receivables_csv">Export receivables</a>} />
    <div className="metric-grid">
      <Metric icon={CircleDollarSign} color="red" label="Outstanding" value={currency.format(Number(data.summary?.outstanding ?? 0))} detail={`${data.summary?.open_count ?? 0} open receivable(s)`} />
      <Metric icon={Activity} color="amber" label="Overdue" value={currency.format(Number(data.summary?.overdue_amount ?? 0))} detail={`${data.summary?.overdue_count ?? 0} past due date`} />
      <Metric icon={PackageCheck} color="green" label="Settled" value={currency.format(Number(data.summary?.total_settled ?? 0))} detail={`${data.summary?.settled_count ?? 0} closed by settlement`} />
      <Metric icon={FileBarChart2} color="violet" label="Written off" value={currency.format(Number(data.summary?.written_off_amount ?? 0))} detail={`${data.summary?.written_off_count ?? 0} written off`} />
    </div>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">01</span><div><h2>Overdue aging</h2><p>Outstanding balances grouped by days past due</p></div></div></div>
      <DataTable headers={['Current', '1–30 days', '31–60 days', '61–90 days', 'Over 90 days']}>
        <tr>
          <td>{currency.format(Number(aging.current || 0))}</td>
          <td>{currency.format(Number(aging['1_30'] || 0))}</td>
          <td>{currency.format(Number(aging['31_60'] || 0))}</td>
          <td>{currency.format(Number(aging['61_90'] || 0))}</td>
          <td>{currency.format(Number(aging.over_90 || 0))}</td>
        </tr>
      </DataTable>
    </section>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">02</span><div><h2>Recovery register</h2><p>{receivables.length} receivable(s) raised from damaged, lost, or discrepant assets</p></div></div></div>
      <DataTable headers={['Reference', 'Borrower / department', 'Description / source', 'Amount / balance', 'Due / status', 'Actions']}>
        {receivables.length ? receivables.map((receivable) => <tr key={receivable.id}>
          <td><strong>{receivable.reference}</strong><small>{formatDate(receivable.issue_date)}</small></td>
          <td>{receivable.borrower}<small>{receivable.department || 'Unassigned department'}</small></td>
          <td>{receivable.description}<small>{receivable.asset_tracking_id ? `Asset record #${receivable.asset_tracking_id}` : ''}{receivable.transfer_id ? ` · Transfer ${receivable.transfer_id}` : ''}{receivable.legacy_expense_id ? ` · Legacy ${receivable.legacy_expense_id}` : ''}</small></td>
          <td>Amount: {currency.format(Number(receivable.amount))}<br/>Settled: {currency.format(Number(receivable.amount_settled))}<br/><strong>Balance: {currency.format(Number(receivable.balance))}</strong></td>
          <td>Due: {formatDate(receivable.due_date)}<br/><Status value={receivable.status} />{receivable.is_overdue && <small className="rejection-note">{receivable.days_overdue} day(s) overdue</small>}{receivable.write_off_reason && <small className="rejection-note">{receivable.write_off_reason}</small>}</td>
          <td>{activeStatuses.includes(receivable.status) && role === 'admin' ? <div className="row-actions"><button type="button" className="text-button success-text" onClick={() => setSettleId(receivable.id)}>Settle</button><button type="button" className="text-button danger-text" onClick={() => setWriteOffId(receivable.id)}>Write off</button></div> : <span className="muted">—</span>}{receivable.settlements?.length > 0 && <small>{receivable.settlements.map((settlement) => `${settlement.settled_on} · ${currency.format(Number(settlement.amount))} · ${settlement.method}`).join(' | ')}</small>}</td>
        </tr>) : <EmptyRow columns={6} text="No recovery receivables recorded yet." />}
      </DataTable>
    </section>

    {settleReceivable && <Modal title={`Settle ${settleReceivable.reference}`} onClose={() => setSettleId('')}>
      <form action={`/recovery_receivables/${settleReceivable.id}/settle`} method="post" onSubmit={(event) => { onSubmit(event); setSettleId(''); }} className="form-grid">
        <FormField label="Settlement amount (KSh)" name="amount" type="number" min="0.01" step="0.01" max={settleReceivable.balance} defaultValue={settleReceivable.balance} required />
        <FormField label="Settlement date" name="settled_on" type="date" defaultValue={todayIso} required />
        <label>Method<select name="method" defaultValue="CASH">{methods.map((method) => <option key={method}>{method}</option>)}</select></label>
        <FormField label="Reference / receipt" name="reference" maxLength="100" />
        <label className="full">Notes<input name="notes" maxLength="500" placeholder="Optional settlement note" /></label>
        <ModalActions onCancel={() => setSettleId('')} />
      </form>
    </Modal>}

    {writeOffReceivable && <Modal title={`Write off ${writeOffReceivable.reference}`} onClose={() => setWriteOffId('')}>
      <form action={`/recovery_receivables/${writeOffReceivable.id}/write_off`} method="post" onSubmit={(event) => { onSubmit(event); setWriteOffId(''); }} className="form-grid">
        <p className="full muted">This removes {currency.format(Number(writeOffReceivable.balance))} from active receivables. The receivable record and reason are retained for audit.</p>
        <label className="full">Write-off reason<input name="reason" maxLength="500" required placeholder="Why this balance will not be recovered" /></label>
        <ModalActions onCancel={() => setWriteOffId('')} />
      </form>
    </Modal>}
  </section>;
}

function FixedAssetsPage({ data, role, onSubmit }) {
  const [disposeAssetId, setDisposeAssetId] = useState('');
  const [editAssetId, setEditAssetId] = useState('');
  if (!data) return <div className="loading">Loading fixed-asset register…</div>;

  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const assets = data.assets || [];
  const categories = data.categories || [];
  const suppliers = data.suppliers || [];
  const departments = data.departments || [];
  const inventoryItems = data.inventory_items || [];
  const activeSuppliers = suppliers.filter((supplier) => supplier.is_active);
  const editAsset = assets.find((asset) => asset.id === editAssetId);
  const disposeAsset = assets.find((asset) => asset.id === disposeAssetId);
  const activeStatuses = ['IN_USE', 'IN_STORE', 'UNDER_REPAIR'];

  const fillAsset = (event) => {
    const item = inventoryItems.find((entry) => String(entry.id) === event.currentTarget.value);
    if (!item) return;
    const form = event.currentTarget.closest('form');
    if (!form) return;
    const name = form.querySelector('input[name="name"]');
    const subcategory = form.querySelector('input[name="subcategory"]');
    const category = form.querySelector('select[name="category"]');
    const cost = form.querySelector('input[name="acquisition_cost"]');
    if (name && !name.value) name.value = item.name;
    if (subcategory && !subcategory.value) subcategory.value = item.subcategory || '';
    if (category && item.category) category.value = item.category;
    if (cost && !cost.value && item.unit_cost) cost.value = item.unit_cost;
  };

  return <section className="page-content fixed-assets-content">
    <PortalHeader title="Fixed asset register" description="Capitalised assets, straight-line depreciation, net book value, and disposals; separate from store stock valuation" actions={<><a className="secondary-button" href="/export_fixed_assets_csv">Export register</a><a className="secondary-button" href="/export_depreciation_csv">Depreciation CSV</a></>} />
    <div className="metric-grid">
      <Metric icon={BriefcaseBusiness} color="blue" label="Active assets" value={data.summary?.active_count ?? 0} detail={`${data.summary?.disposed_count ?? 0} disposed / written off`} />
      <Metric icon={CircleDollarSign} color="green" label="Acquisition cost" value={currency.format(Number(data.summary?.total_cost ?? 0))} detail="Active register only" />
      <Metric icon={Activity} color="amber" label="Accumulated depreciation" value={currency.format(Number(data.summary?.total_accumulated ?? 0))} detail={`${currency.format(Number(data.summary?.monthly_depreciation ?? 0))} monthly charge`} />
      <Metric icon={PackageCheck} color="violet" label="Net book value" value={currency.format(Number(data.summary?.total_nbv ?? 0))} detail={`${data.summary?.entry_count ?? 0} posted entries`} />
    </div>

    {role === 'admin' && <section className="section-card">
      <div className="section-title"><div><span className="section-index">01</span><div><h2>Run depreciation</h2><p>Posts all missing monthly straight-line entries through the selected period; each asset/period can only be posted once</p></div></div></div>
      <form action="/fixed_assets/depreciate" method="post" onSubmit={onSubmit} className="portal-form">
        <FormField label="Depreciation through period" name="period" type="month" defaultValue={data.summary?.current_period} required />
        <button className="primary-button" type="submit">Post depreciation</button>
      </form>
    </section>}

    {role === 'admin' && <section className="section-card">
      <div className="section-title"><div><span className="section-index">02</span><div><h2>Add fixed asset</h2><p>Record a capitalised asset and its depreciation basis</p></div></div></div>
      <form action="/fixed_assets/add" method="post" onSubmit={onSubmit} className="portal-form">
        <FormField label="Asset name" name="name" maxLength="150" required />
        <label>Category<select name="category" defaultValue={categories[0] || 'Other'}>{categories.map((category) => <option key={category}>{category}</option>)}</select></label>
        <FormField label="Subcategory" name="subcategory" maxLength="60" />
        <label>Inventory item (optional)<select name="inventory_item_id" defaultValue="" onChange={fillAsset}><option value="">No linked store item</option>{inventoryItems.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.category}</option>)}</select></label>
        <label>Supplier (optional)<select name="supplier_id" defaultValue=""><option value="">Unassigned supplier</option>{activeSuppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
        <label>Department (optional)<select name="department_id" defaultValue=""><option value="">Unassigned department</option>{departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>
        <FormField label="Serial number" name="serial_number" maxLength="120" />
        <FormField label="Location" name="location" maxLength="120" />
        <FormField label="Custodian" name="custodian" maxLength="120" />
        <FormField label="Acquisition date" name="acquisition_date" type="date" defaultValue={todayIso} required />
        <FormField label="Total acquisition cost (KSh)" name="acquisition_cost" type="number" min="0.01" step="0.01" required />
        <FormField label="Quantity" name="quantity" type="number" min="1" defaultValue="1" required />
        <FormField label="Salvage value (KSh)" name="salvage_value" type="number" min="0" step="0.01" defaultValue="0" />
        <FormField label="Useful life (months)" name="useful_life_months" type="number" min="1" max="600" defaultValue="60" required />
        <label>Status<select name="status" defaultValue="IN_USE"><option value="IN_USE">In use</option><option value="IN_STORE">In store</option><option value="UNDER_REPAIR">Under repair</option></select></label>
        <FormField label="Description" name="description" maxLength="500" />
        <FormField label="Asset tag (optional)" name="asset_tag" maxLength="32" />
        <button className="primary-button" type="submit">Add fixed asset</button>
      </form>
    </section>}

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">03</span><div><h2>Asset register</h2><p>{assets.length} asset(s) in the accounting register</p></div></div></div>
      <DataTable headers={['Asset', 'Category / status', 'Acquisition', 'Cost / NBV', 'Depreciation', 'Location / custodian', 'Actions']}>
        {assets.length ? assets.map((asset) => <tr key={asset.id}>
          <td><strong>{asset.name}</strong><small>{asset.asset_tag} · {asset.serial_number || '—'}</small></td>
          <td>{asset.category}<small>{asset.subcategory || '—'} · {asset.department || 'Unassigned department'}</small><Status value={asset.status} /></td>
          <td>{formatDate(asset.acquisition_date)}<small>Qty {asset.quantity}</small></td>
          <td>Cost: {currency.format(Number(asset.acquisition_cost))}<br/><strong>NBV: {currency.format(Number(asset.net_book_value))}</strong></td>
          <td>Accum: {currency.format(Number(asset.accumulated_depreciation))}<br/><small>{currency.format(Number(asset.monthly_depreciation))}/month · {asset.useful_life_months} months</small></td>
          <td>{asset.location || '—'}<small>{asset.custodian || '—'}{asset.supplier && ` · ${asset.supplier}`}</small></td>
          <td>{['DISPOSED', 'LOST'].includes(asset.status) ? <small>{asset.status === 'DISPOSED' ? 'Disposed' : 'Written off'} {formatDate(asset.disposal_date)} · {currency.format(Number(asset.gain_loss_on_disposal || 0))} gain/loss</small> : role === 'admin' ? <div className="row-actions"><button type="button" className="text-button" onClick={() => setEditAssetId(asset.id)}>Edit</button><button type="button" className="text-button danger-text" onClick={() => setDisposeAssetId(asset.id)}>Dispose</button></div> : <span className="muted">—</span>}</td>
        </tr>) : <EmptyRow columns={7} text="No fixed assets recorded yet." />}
      </DataTable>
    </section>

    <section className="section-card">
      <div className="section-title"><div><span className="section-index">04</span><div><h2>Depreciation history</h2><p>Most recent posted entries</p></div></div></div>
      <DataTable headers={['Period', 'Asset', 'Amount', 'Accumulated after', 'Posted']}>
        {(data.depreciation_entries || []).length ? data.depreciation_entries.map((entry) => <tr key={entry.id}><td>{entry.period}</td><td>{entry.asset_tag} · {entry.asset_name}</td><td>{currency.format(Number(entry.amount))}</td><td>{currency.format(Number(entry.accumulated_after))}</td><td>{formatDate(entry.created_at)}<small>{entry.created_by || '—'}</small></td></tr>) : <EmptyRow columns={5} text="No depreciation posted yet." />}
      </DataTable>
    </section>

    {editAsset && <Modal title={`Edit ${editAsset.asset_tag}`} onClose={() => setEditAssetId('')}>
      <form action={`/fixed_assets/${editAsset.id}/update`} method="post" onSubmit={(event) => { onSubmit(event); setEditAssetId(''); }} className="form-grid">
        <FormField label="Asset name" name="name" maxLength="150" defaultValue={editAsset.name} required />
        <label>Category<select name="category" defaultValue={editAsset.category}>{categories.map((category) => <option key={category}>{category}</option>)}</select></label>
        <FormField label="Subcategory" name="subcategory" maxLength="60" defaultValue={editAsset.subcategory || ''} />
        <FormField label="Serial number" name="serial_number" maxLength="120" defaultValue={editAsset.serial_number || ''} />
        <FormField label="Location" name="location" maxLength="120" defaultValue={editAsset.location || ''} />
        <FormField label="Custodian" name="custodian" maxLength="120" defaultValue={editAsset.custodian || ''} />
        <label>Department<select name="department_id" defaultValue={editAsset.department_id || ''}><option value="">Unassigned department</option>{departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>
        <label>Supplier<select name="supplier_id" defaultValue={editAsset.supplier_id || ''}><option value="">Unassigned supplier</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></label>
        <FormField label="Useful life (months)" name="useful_life_months" type="number" min="1" max="600" defaultValue={editAsset.useful_life_months} disabled={Number(editAsset.accumulated_depreciation) > 0} required />
        <FormField label="Salvage value (KSh)" name="salvage_value" type="number" min="0" step="0.01" defaultValue={editAsset.salvage_value} disabled={Number(editAsset.accumulated_depreciation) > 0} />
        <label>Status<select name="status" defaultValue={editAsset.status}><option value="IN_USE">In use</option><option value="IN_STORE">In store</option><option value="UNDER_REPAIR">Under repair</option></select></label>
        <FormField label="Description" name="description" maxLength="500" defaultValue={editAsset.description || ''} />
        {Number(editAsset.accumulated_depreciation) > 0 && <p className="full muted">Useful life and salvage are locked after depreciation has been posted.</p>}
        <ModalActions onCancel={() => setEditAssetId('')} />
      </form>
    </Modal>}

    {disposeAsset && <Modal title={`Dispose ${disposeAsset.asset_tag}`} onClose={() => setDisposeAssetId('')}>
      <form action={`/fixed_assets/${disposeAsset.id}/dispose`} method="post" onSubmit={(event) => { onSubmit(event); setDisposeAssetId(''); }} className="form-grid">
        <label>Disposal type<select name="disposal_type" defaultValue="DISPOSED"><option value="DISPOSED">Sold / disposed</option><option value="LOST">Lost / written off</option></select></label>
        <FormField label="Disposal date" name="disposal_date" type="date" defaultValue={todayIso} required />
        <FormField label="Proceeds (KSh)" name="disposal_proceeds" type="number" min="0" step="0.01" defaultValue="0" />
        <label className="full">Reason<input name="disposal_reason" maxLength="500" required placeholder="Sale, write-off, loss, or disposal reason" /></label>
        <p className="full muted">Post depreciation through the month before disposal first; disposal does not backfill missing depreciation.</p>
        <ModalActions onCancel={() => setDisposeAssetId('')} />
      </form>
    </Modal>}
  </section>;
}

function InventoryPage({ data, role, onSubmit, view }) {
  const [tab, setTab] = useState(() => view === 'inventory' ? 'all' : 'requisitions');
  const [modal, setModal] = useState('');
  const [category, setCategory] = useState(Object.keys(data?.categories || {})[0] || '');
  const [itemQuery, setItemQuery] = useState('');
  const categories = data?.categories || {};
  useEffect(() => {
    if (!category && Object.keys(categories).length) setCategory(Object.keys(categories)[0]);
  }, [category, categories]);
  useEffect(() => {
    setTab(view === 'inventory' ? 'all' : 'requisitions');
  }, [view]);
  const visibleItems = useMemo(() => (data?.items || []).filter((item) =>
    `${item.name} ${item.category} ${item.subcategory}`.toLowerCase().includes(itemQuery.toLowerCase())
  ), [data, itemQuery]);

  if (!data) return <div className="loading">Loading store records…</div>;
  const consumables = data.items.filter((item) => item.is_consumable);
  const durable = data.items.filter((item) => !item.is_consumable);
  const lowStockItems = data.items.filter(
    (item) => item.current_stock <= item.reorder_level
  );

  return (
    <section className="page-content inventory-content">
      <header className="page-heading">
        <div className="club-heading"><div className="club-emblem">TGC</div><div><h1>Store Requisitions & Asset Inventory</h1><p>Control stock, department requests, and durable equipment</p></div></div>
        <div className="header-actions"><a className="secondary-button" href="/export_inventory_csv">Export valuation</a><button className="secondary-button" onClick={() => window.print()}><Printer size={16} /> Print</button>{role === 'admin' && <button className="primary-button" onClick={() => setModal('add')}><Plus size={16} /> Add store item</button>}</div>
      </header>
      <div className="metric-grid inventory-metrics">
        <Metric icon={CircleDollarSign} color="green" label="Stock value" value={currency.format(Number(data.summary?.total_value ?? 0))} detail={`${data.items.length} items · ${lowStockItems.length} low stock`} />
        <Metric icon={PackageCheck} color="violet" label="Consumable value" value={currency.format(Number(data.summary?.consumable_value ?? 0))} detail={`${consumables.length} consumable item(s)`} />
        <Metric icon={BriefcaseBusiness} color="blue" label="Durable asset value" value={currency.format(Number(data.summary?.durable_value ?? 0))} detail={`${durable.length} items · ${currency.format(Number(data.summary?.assets_out_value ?? 0))} out`} />
        <Metric icon={Boxes} color="amber" label="Low stock" value={lowStockItems.length} detail={`${currency.format(Number(data.summary?.low_stock_value ?? 0))} at risk`} />
        <Metric icon={ClipboardList} color="red" label="Pending requisitions" value={data.summary?.pending_requisition_count ?? data.requisitions.filter((entry) => entry.status === 'PENDING').length} detail={currency.format(Number(data.summary?.pending_requisition_value ?? 0))} />
      </div>

      <section className="section-card">
        <div className="section-title"><div><span className="section-index">01</span><div><h2>Consumable store requisitions</h2><p>Requests require review before stock is issued</p></div></div><button className="secondary-button" onClick={() => setModal('request')}><Plus size={16} /> New request</button></div>
        <div className="table-wrap">
          <table><thead><tr><th>Request ID</th><th>Requester</th><th>Department</th><th>Item</th><th>Quantity</th><th>Value</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>{data.requisitions.length ? data.requisitions.map((request) => <tr key={request.id}><td className="mono">#{request.id}</td><td>{request.requester}</td><td>{request.department}</td><td>{request.item}</td><td>{request.quantity} {request.unit_of_measure}</td><td>{request.value ? currency.format(Number(request.value)) : '—'}</td><td><Status value={request.status} />{request.rejection_reason && <small className="rejection-note">{request.rejection_reason}</small>}</td><td>{request.status === 'PENDING' && role === 'admin' ? <div className="row-actions"><form action={`/store/approve-issue/${request.id}`} method="post" onSubmit={onSubmit}><button className="text-button success-text"><Check size={14} /> Approve & issue</button></form><form action={`/store/reject/${request.id}`} method="post" onSubmit={onSubmit} className="reject-form"><input name="reason" placeholder="Reason required to reject" maxLength="500" required aria-label="Rejection reason" /><button className="text-button danger-text">Reject</button></form></div> : <span className="muted">—</span>}</td></tr>) : <EmptyRow columns={8} text="No requisitions recorded yet." />}</tbody>
          </table>
        </div>
      </section>
      <section className="section-card">
        <div className="section-title"><div><span className="section-index">02</span><div><h2>Durable assets & equipment lifecycle</h2><p>Borrowed items remain assigned until inspected and returned</p></div></div>{role === 'admin' && <button className="secondary-button" onClick={() => setModal('transfer')}><ArrowLeftRight size={16} /> Record asset issue</button>}</div>
        <div className="table-wrap">
          <table><thead><tr><th>Asset</th><th>Borrowed by</th><th>Department</th><th>Quantity</th><th>Value</th><th>Date out</th><th>Status</th><th>Returns</th></tr></thead>
            <tbody>{data.active_assets.length ? data.active_assets.map((asset) => <tr key={asset.id}><td>{asset.item}</td><td>{asset.borrower}</td><td>{asset.department}</td><td>{asset.quantity} {asset.unit_of_measure}</td><td>{asset.value ? currency.format(Number(asset.value)) : '—'}</td><td>{formatDate(asset.borrowed_at)}</td><td><Status value={asset.status || 'OUT'} /></td><td>{asset.transfer_id ? <a className="text-button" href="/transfers">Reconcile via {asset.transfer_id}</a> : role === 'admin' ? <form action={`/assets/return/${asset.id}`} method="post" onSubmit={onSubmit} className="inline-form"><select name="condition" defaultValue="GOOD" aria-label="Return condition" onChange={(event) => { event.currentTarget.form.elements.inspection_notes.required = event.target.value === 'DAMAGED_LOST'; }}><option value="GOOD">Good</option><option value="DAMAGED_LOST">Damaged/lost</option></select><input name="inspection_notes" placeholder="Inspection notes" maxLength="2000" /><button className="text-button success-text">Return</button></form> : <span className="muted">Admin inspection required</span>}</td></tr>) : <EmptyRow columns={8} text="No assets are currently checked out." />}</tbody>
          </table>
        </div>
      </section>

      <section className="section-card">
        <div className="section-title"><div><span className="section-index">03</span><div><h2>Stock catalogue</h2><p>Category and subcategory are separate from consumable/durable tracking</p></div></div>{role === 'admin' && <div className="row-actions"><button className="secondary-button" onClick={() => setModal('receive')}><ArrowDownToLine size={16} /> Receive stock</button><button className="secondary-button" onClick={() => setModal('count')}>Physical count</button></div>}</div>
        <div className="toolbar"><div className="tabs"><button className={tab === 'all' ? 'tab-current' : ''} onClick={() => setTab('all')}>All items <span>{data.items.length}</span></button><button className={tab === 'requisitions' ? 'tab-current' : ''} onClick={() => setTab('requisitions')}>Consumables only <span>{consumables.length}</span></button></div><label className="table-search"><Search size={16} /><input value={itemQuery} onChange={(event) => setItemQuery(event.target.value)} placeholder="Search catalogue" /></label></div>
        <div className="table-wrap"><table><thead><tr><th>Item</th><th>Category</th><th>Subcategory</th><th>Type</th><th>On hand</th><th>Reorder level</th><th>Unit cost</th><th>Stock value</th><th>Department</th></tr></thead><tbody>
          {visibleItems.length ? visibleItems.filter((item) => tab !== 'requisitions' || item.is_consumable).map((item) => <tr key={item.id}><td className="item-cell"><strong>{item.name}</strong><small>#{item.id}</small></td><td>{item.category}</td><td>{item.subcategory || 'Other'}</td><td><span className="type-pill">{item.is_consumable ? 'Consumable' : 'Durable asset'}</span></td><td className={item.current_stock <= item.reorder_level ? 'stock-low' : ''}>{item.current_stock} {item.unit_of_measure}</td><td>{item.reorder_level}</td><td>{item.unit_cost ? currency.format(Number(item.unit_cost)) : '—'}</td><td>{item.stock_value ? currency.format(Number(item.stock_value)) : '—'}</td><td>{item.department || '—'}</td></tr>) : <EmptyRow columns={9} text="No matching inventory items." />}
        </tbody></table></div>
      </section>

      <section className="section-card">
        <div className="section-title"><div><span className="section-index">04</span><div><h2>Recent stock movements</h2><p>Trace receipts and issues in the stock ledger</p></div></div></div>
        <div className="table-wrap"><table><thead><tr><th>Date</th><th>Item</th><th>Movement</th><th>Quantity</th><th>Unit cost</th><th>Value</th><th>Balance</th><th>Reference</th><th>Department</th></tr></thead><tbody>{data.movements.length ? data.movements.map((movement) => <tr key={movement.id}><td>{formatDate(movement.created_at)}</td><td>{movement.item}</td><td><Status value={movement.movement_type} />{movement.reason_code && <small className="rejection-note">{movement.reason_code}</small>}</td><td>{movement.quantity}</td><td>{movement.unit_cost ? currency.format(Number(movement.unit_cost)) : '—'}</td><td>{movement.total_value ? currency.format(Number(movement.total_value)) : '—'}</td><td>{movement.balance_after ?? '—'}</td><td>{movement.reference || '—'}</td><td>{movement.department || '—'}</td></tr>) : <EmptyRow columns={9} text="No stock movements recorded." />}</tbody></table></div>
      </section>

      <section className="section-card">
        <div className="section-title"><div><span className="section-index">05</span><div><h2>Consumable consumption by department</h2><p>Issued quantities valued at the cost snapshot recorded in the stock ledger</p></div></div></div>
        <div className="table-wrap"><table><thead><tr><th>Department</th><th>Issues</th><th>Quantity issued</th><th>Issued value</th></tr></thead><tbody>{data.consumption_by_department.length ? data.consumption_by_department.map((row) => <tr key={row.department}><td>{row.department}</td><td>{row.movements}</td><td>{row.quantity}</td><td>{currency.format(Number(row.value))}</td></tr>) : <EmptyRow columns={4} text="No consumable issues recorded yet." />}</tbody></table></div>
      </section>

      {data.recovery_cases.length > 0 && <section className="section-card"><div className="section-title"><div><span className="section-index">06</span><div><h2>Damage, loss & recovery</h2><p>Review exceptions and follow the recovery trail</p></div></div></div><div className="table-wrap"><table><thead><tr><th>Asset</th><th>Borrower</th><th>Department</th><th>Quantity</th><th>Condition</th><th>Recovery document</th></tr></thead><tbody>{data.recovery_cases.map((record) => <tr key={record.id}><td>{record.item}</td><td>{record.borrower}</td><td>{record.department}</td><td>{record.quantity}</td><td><Status value={record.status} /></td><td>{record.recovery_receivable_reference ? <a className="text-button success-text" href="/recovery_receivables">Receivable {record.recovery_receivable_reference}</a> : record.recovery_expense_id ? <span className="muted">Legacy {record.recovery_expense_id}</span> : role === 'admin' ? <form action={`/assets/recovery/${record.id}`} method="post" onSubmit={onSubmit}><button className="text-button success-text">Raise recovery receivable</button></form> : 'Awaiting administrator'}</td></tr>)}</tbody></table></div></section>}

      {modal && <Modal title={{ add: 'Add store item', request: 'New consumable requisition', transfer: 'Issue a durable asset', receive: 'Receive stock', count: 'Record physical count' }[modal]} onClose={() => setModal('')}>
        {modal === 'add' && <form action="/inventory/add" method="post" onSubmit={(event) => { onSubmit(event); setModal(''); }} className="form-grid">
          <label className="full">Item name<input name="name" maxLength="120" required /></label>
          <label>Category<select name="category" value={category} onChange={(event) => setCategory(event.target.value)}>{Object.keys(categories).map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Subcategory<select name="subcategory" key={category}>{(categories[category] || []).map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Unit of measure<input name="unit_of_measure" maxLength="30" defaultValue="Units" required /></label>
          <label>Item tracking<select name="is_consumable"><option value="true">Consumable</option><option value="false">Durable asset / tool</option></select></label>
          <label>Opening quantity<input name="current_stock" type="number" min="0" defaultValue="0" required /></label>
          <label>Reorder level<input name="reorder_level" type="number" min="0" defaultValue="10" required /></label>
          <label className="full">Opening unit cost (required when opening stock is above zero)<input name="unit_cost" type="number" min="0.01" step="0.01" /></label>
          <ModalActions onCancel={() => setModal('')} />
        </form>}
        {modal === 'request' && <form action="/store/request" method="post" onSubmit={(event) => { onSubmit(event); setModal(''); }} className="form-grid">
          <label className="full">Consumable item<select name="item_id" required><option value="">Select an item</option>{consumables.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.current_stock} {item.unit_of_measure})</option>)}</select></label>
          <label>Quantity<input name="quantity" type="number" min="1" required /></label>
          <label>Department<select name="department_id" required><option value="">Select department</option>{data.departments.map((dept) => <option key={dept.id} value={dept.id}>{dept.name}</option>)}</select></label>
          <label className="full">Requester name<input name="requester_name" maxLength="100" required /></label><ModalActions onCancel={() => setModal('')} />
        </form>}
        {modal === 'transfer' && <form action="/assets/transfer" method="post" onSubmit={(event) => { onSubmit(event); setModal(''); }} className="form-grid">
          <label className="full">Durable asset<select name="item_id" required><option value="">Select an item</option>{durable.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.current_stock} {item.unit_of_measure} available)</option>)}</select></label>
          <label>Quantity<input name="quantity" type="number" min="1" required /></label>
          <label>Department<select name="department_id" required><option value="">Select department</option>{data.departments.map((dept) => <option key={dept.id} value={dept.id}>{dept.name}</option>)}</select></label>
          <label className="full">Borrower / custodian<input name="borrower_name" maxLength="100" required /></label><ModalActions onCancel={() => setModal('')} />
        </form>}
        {modal === 'receive' && <form action="/inventory/receive" method="post" onSubmit={(event) => { onSubmit(event); setModal(''); }} className="form-grid">
          <label className="full">Item<select name="item_id" required><option value="">Select item</option>{data.items.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.current_stock} on hand)</option>)}</select></label>
          <label>Quantity received<input name="quantity" type="number" min="1" required /></label><label>Unit cost (KSh)<input name="unit_cost" type="number" min="0.01" step="0.01" required /></label>
          <label className="full">Supplier / invoice / GRN reference<input name="reference" maxLength="100" required /></label><ModalActions onCancel={() => setModal('')} />
        </form>}
        {modal === 'count' && <form action="/inventory/adjust" method="post" onSubmit={(event) => { onSubmit(event); setModal(''); }} className="form-grid">
          <label className="full">Item<select name="item_id" required><option value="">Select item</option>{data.items.map((item) => <option key={item.id} value={item.id}>{item.name} (book: {item.current_stock})</option>)}</select></label>
          <label className="full">Physical quantity counted<input name="counted_stock" type="number" min="0" required /></label><label>Reason code<select name="reason_code" defaultValue="COUNT_CORRECTION"><option value="COUNT_CORRECTION">Count correction</option><option value="DAMAGE">Damage</option><option value="THEFT">Theft / loss</option><option value="EXPIRY">Expiry</option><option value="OTHER">Other</option></select></label><label className="full">Reason / count reference<input name="reason" maxLength="500" required /></label><ModalActions onCancel={() => setModal('')} />
        </form>}
      </Modal>}
    </section>
  );
}

function Modal({ title, children, onClose }) {
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><header><div><h2>{title}</h2><p>Changes are recorded in the existing store ledger.</p></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></header>{children}</section></div>;
}

function ModalActions({ onCancel }) {
  return <div className="modal-actions full"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button className="primary-button" type="submit">Save and continue <Check size={16} /></button></div>;
}

function Status({ value }) {
  const normal = (value || '').toUpperCase();
  const color = ['ISSUED', 'IN', 'RETURNED_GOOD', 'APPROVED', 'IN_USE', 'IN_STORE', 'ACTIVE', 'RECEIVED', 'MATCHED', 'FULLY_RETURNED'].includes(normal) ? 'green' : ['OUT'].includes(normal) ? 'blue' : ['DAMAGED_LOST', 'REJECTED', 'DISPOSED', 'LOST', 'VARIANCE'].includes(normal) ? 'red' : 'amber';
  const label = (value || 'Unknown').replaceAll('_', ' ');
  return <span className={`status status-${color}`}>{label}</span>;
}

function EmptyRow({ columns, text }) {
  return <tr><td className="empty-row" colSpan={columns}>{text}</td></tr>;
}

function ModalLogin({ onSubmit, error, bootstrapRequired }) {
  return <main className="login-page"><section className="login-card"><div className="login-brand">TGC</div><p className="eyebrow">THIKA GYMKHANA CLUB</p><h1>Welcome back</h1><p className="login-intro">Sign in to manage club operations.</p>{bootstrapRequired ? <div className="setup-notice">No accounts exist yet. Set <code>INITIAL_ADMIN_PASSWORD</code> (and optionally <code>INITIAL_ADMIN_USERNAME</code>) in the environment, then restart the app to create the first administrator.</div> : error && <div className="alert alert-error">{error}</div>}<form onSubmit={onSubmit} className="login-form"><label>Username<input name="username" autoComplete="username" required autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label><button className="primary-button" type="submit">Sign in</button></form><small>Access is controlled by your club account permissions.</small></section></main>;
}

const Login = ModalLogin;

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

createRoot(document.getElementById('root')).render(<App />);
