import { useState, useEffect } from "react";

const SAMPLE_CASES = [
  {
    id: "SAMPLE-01",
    label: "Wrong Transfer - Consistent",
    complaint: "I sent 5000 taka to a wrong number around 2pm today. The number was supposed to be 01712345678 but I think I typed it wrong. The person isn't responding to my call. Please help me get my money back.",
    language: "en",
    channel: "in_app_chat",
    user_type: "customer",
    campaign_context: "boishakh_bonanza_day_1",
    transaction_history: [
      { transaction_id: "TXN-9101", timestamp: "2026-04-14T14:08:22Z", type: "transfer", amount: 5000, counterparty: "+8801719876543", status: "completed" },
      { transaction_id: "TXN-9087", timestamp: "2026-04-13T18:12:00Z", type: "cash_in", amount: 10000, counterparty: "AGENT-512", status: "completed" }
    ]
  },
  {
    id: "SAMPLE-02",
    label: "Wrong Transfer - Inconsistent",
    complaint: "I sent 2000 to the wrong person by mistake. Please reverse it.",
    language: "en",
    channel: "in_app_chat",
    user_type: "customer",
    campaign_context: "",
    transaction_history: [
      { transaction_id: "TXN-9202", timestamp: "2026-04-14T11:30:00Z", type: "transfer", amount: 2000, counterparty: "+8801812345678", status: "completed" },
      { transaction_id: "TXN-9180", timestamp: "2026-04-10T09:15:00Z", type: "transfer", amount: 2500, counterparty: "+8801812345678", status: "completed" },
      { transaction_id: "TXN-9145", timestamp: "2026-04-05T17:45:00Z", type: "transfer", amount: 1500, counterparty: "+8801812345678", status: "completed" }
    ]
  },
  {
    id: "SAMPLE-03",
    label: "Failed Payment - Consistent",
    complaint: "I tried to pay 1200 taka for my mobile recharge but the app showed failed. But my balance was deducted! Please refund my money.",
    language: "en",
    channel: "in_app_chat",
    user_type: "customer",
    campaign_context: "",
    transaction_history: [
      { transaction_id: "TXN-9301", timestamp: "2026-04-14T16:00:00Z", type: "payment", amount: 1200, counterparty: "MERCHANT-MOBILE-OP", status: "failed" }
    ]
  },
  {
    id: "SAMPLE-04",
    label: "Refund Request - Customer Change",
    complaint: "I paid 500 to a merchant for a product but I changed my mind and don't want it anymore. Please refund my 500 taka.",
    language: "en",
    channel: "in_app_chat",
    user_type: "customer",
    campaign_context: "",
    transaction_history: [
      { transaction_id: "TXN-9401", timestamp: "2026-04-14T13:00:00Z", type: "payment", amount: 500, counterparty: "MERCHANT-7821", status: "completed" }
    ]
  },
  {
    id: "SAMPLE-05",
    label: "Phishing Attempt - OTP Prompt",
    complaint: "Someone called me saying they are from bKash and asked for my OTP. They said my account will be blocked if I don't share it. Is this real? I haven't shared anything yet.",
    language: "en",
    channel: "call_center",
    user_type: "customer",
    campaign_context: "",
    transaction_history: []
  },
  {
    id: "SAMPLE-07",
    label: "Agent Cash-in (Bangla)",
    complaint: "আমি আজ সকালে এজেন্টের কাছে ২০০০ টাকা ক্যাশ ইন করেছি কিন্তু আমার ব্যালেন্সে টাকা আসেনি। এজেন্ট বলছে টাকা পাঠিয়েছে কিন্তু আমি দেখছি না।",
    language: "bn",
    channel: "call_center",
    user_type: "customer",
    campaign_context: "",
    transaction_history: [
      { transaction_id: "TXN-9701", timestamp: "2026-04-14T09:30:00Z", type: "cash_in", amount: 2000, counterparty: "AGENT-318", status: "pending" }
    ]
  },
  {
    id: "SAMPLE-10",
    label: "Duplicate Payment - Consistent",
    complaint: "I paid my electricity bill 850 taka but it deducted twice from my account. Please check, I only paid once.",
    language: "en",
    channel: "in_app_chat",
    user_type: "customer",
    campaign_context: "",
    transaction_history: [
      { transaction_id: "TXN-10001", timestamp: "2026-04-14T08:15:30Z", type: "payment", amount: 850, counterparty: "BILLER-DESCO", status: "completed" },
      { transaction_id: "TXN-10002", timestamp: "2026-04-14T08:15:42Z", type: "payment", amount: 850, counterparty: "BILLER-DESCO", status: "completed" }
    ]
  }
];

export default function App() {
  const [ticketId, setTicketId] = useState("TKT-101");
  const [complaint, setComplaint] = useState("");
  const [language, setLanguage] = useState("en");
  const [channel, setChannel] = useState("in_app_chat");
  const [userType, setUserType] = useState("customer");
  const [campaignContext, setCampaignContext] = useState("");
  const [txnHistory, setTxnHistory] = useState([]);
  
  // Form add-row states
  const [newTxnId, setNewTxnId] = useState("");
  const [newTimestamp, setNewTimestamp] = useState("");
  const [newType, setNewType] = useState("transfer");
  const [newAmount, setNewAmount] = useState("");
  const [newCounterparty, setNewCounterparty] = useState("");
  const [newStatus, setNewStatus] = useState("completed");

  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [apiHealth, setApiHealth] = useState({ status: "checking" });

  useEffect(() => {
    checkHealth();
  }, []);

  const checkHealth = () => {
    fetch("/health")
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error("unreachable");
      })
      .then((data) => {
        if (data.status === "ok") {
          setApiHealth({ status: "healthy" });
        } else {
          setApiHealth({ status: "unhealthy" });
        }
      })
      .catch(() => {
        setApiHealth({ status: "unreachable" });
      });
  };

  const loadCase = (c) => {
    setTicketId(`TKT-${Math.floor(100 + Math.random() * 900)}`);
    setComplaint(c.complaint);
    setLanguage(c.language || "en");
    setChannel(c.channel || "in_app_chat");
    setUserType(c.user_type || "customer");
    setCampaignContext(c.campaign_context || "");
    setTxnHistory(c.transaction_history || []);
    setResponse(null);
    setError(null);
  };

  const handleAddTxn = (e) => {
    e.preventDefault();
    if (!newTxnId || !newAmount || !newCounterparty) return;
    
    const newEntry = {
      transaction_id: newTxnId,
      timestamp: newTimestamp || new Date().toISOString(),
      type: newType,
      amount: parseFloat(newAmount),
      counterparty: newCounterparty,
      status: newStatus
    };
    
    setTxnHistory([...txnHistory, newEntry]);
    
    // Clear inputs
    setNewTxnId("");
    setNewTimestamp("");
    setNewAmount("");
    setNewCounterparty("");
  };

  const handleRemoveTxn = (idx) => {
    setTxnHistory(txnHistory.filter((_, i) => i !== idx));
  };

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!complaint.trim()) return;

    setLoading(true);
    setError(null);
    setResponse(null);
    setCopied(false);

    try {
      const res = await fetch("/analyze-ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticket_id: ticketId,
          complaint,
          language: language || null,
          channel: channel || null,
          user_type: userType || null,
          campaign_context: campaignContext || null,
          transaction_history: txnHistory,
          metadata: {}
        })
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.error || errData.details || "Request failed");
      }

      const data = await res.json();
      setResponse(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCopyReply = () => {
    if (!response || !response.customer_reply) return;
    navigator.clipboard.writeText(response.customer_reply);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="brand">
          <span className="brand-logo">⚡</span>
          <div>
            <h1>QueueStorm Investigator</h1>
            <p>Preliminary SupportOps Analysis Console</p>
          </div>
        </div>
        <div className="system-status">
          <span className="status-label">API Health:</span>
          <span className={`status-badge status-${apiHealth.status}`} onClick={checkHealth}>
            {apiHealth.status.toUpperCase()}
          </span>
        </div>
      </header>

      <main className="app-content">
        {/* Sidebar Case Selector */}
        <section className="sidebar-section">
          <h2>Quick Test Templates</h2>
          <p>Load mock CRM cases to populate inputs:</p>
          <div className="case-list">
            {SAMPLE_CASES.map((c) => (
              <button key={c.id} className="case-btn" onClick={() => loadCase(c)}>
                <div className="case-btn-header">
                  <span className="case-tag">{c.id}</span>
                  <span className="case-lang">{c.language.toUpperCase()}</span>
                </div>
                <div className="case-btn-label">{c.label}</div>
              </button>
            ))}
          </div>
        </section>

        {/* Input Form & History */}
        <section className="input-section">
          <h2>Ticket Input Parameters</h2>
          <form onSubmit={handleAnalyze}>
            <div className="form-row">
              <div className="form-group col-6">
                <label>Ticket ID</label>
                <input type="text" value={ticketId} onChange={(e) => setTicketId(e.target.value)} required />
              </div>
              <div className="form-group col-6">
                <label>Context Language</label>
                <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                  <option value="en">English (en)</option>
                  <option value="bn">Bangla (bn)</option>
                  <option value="mixed">Mixed / Benglish (mixed)</option>
                </select>
              </div>
            </div>

            <div className="form-row">
              <div className="form-group col-4">
                <label>Channel</label>
                <select value={channel} onChange={(e) => setChannel(e.target.value)}>
                  <option value="in_app_chat">In App Chat</option>
                  <option value="call_center">Call Center</option>
                  <option value="email">Email</option>
                  <option value="merchant_portal">Merchant Portal</option>
                  <option value="field_agent">Field Agent</option>
                </select>
              </div>
              <div className="form-group col-4">
                <label>User Type</label>
                <select value={userType} onChange={(e) => setUserType(e.target.value)}>
                  <option value="customer">Customer</option>
                  <option value="merchant">Merchant</option>
                  <option value="agent">Agent</option>
                  <option value="unknown">Unknown</option>
                </select>
              </div>
              <div className="form-group col-4">
                <label>Campaign Context</label>
                <input type="text" placeholder="e.g. bonanza_day_1" value={campaignContext} onChange={(e) => setCampaignContext(e.target.value)} />
              </div>
            </div>

            <div className="form-group">
              <label>Customer Complaint Message</label>
              <textarea 
                rows="4" 
                placeholder="Write customer message here..."
                value={complaint} 
                onChange={(e) => setComplaint(e.target.value)} 
                required 
              />
            </div>

            <div className="txn-history-container">
              <h3>Customer Transaction History ({txnHistory.length})</h3>
              
              {txnHistory.length === 0 ? (
                <div className="empty-txn">No transactions in history (typical for non-transaction complaints).</div>
              ) : (
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th>Txn ID</th>
                      <th>Type</th>
                      <th>Amount</th>
                      <th>Counterparty</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {txnHistory.map((t, idx) => (
                      <tr key={t.transaction_id}>
                        <td><code>{t.transaction_id}</code></td>
                        <td><span className="txn-type">{t.type}</span></td>
                        <td>{t.amount} BDT</td>
                        <td><code>{t.counterparty}</code></td>
                        <td><span className={`txn-status status-${t.status}`}>{t.status}</span></td>
                        <td>
                          <button type="button" className="remove-txn-btn" onClick={() => handleRemoveTxn(idx)}>✕</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Add Transaction Entry Panel */}
              <div className="add-txn-panel">
                <h4>Add Transaction Log</h4>
                <div className="txn-form-row">
                  <input type="text" placeholder="TXN-ID" value={newTxnId} onChange={(e) => setNewTxnId(e.target.value)} />
                  <select value={newType} onChange={(e) => setNewType(e.target.value)}>
                    <option value="transfer">Transfer</option>
                    <option value="payment">Payment</option>
                    <option value="cash_in">Cash-in</option>
                    <option value="cash_out">Cash-out</option>
                    <option value="settlement">Settlement</option>
                    <option value="refund">Refund</option>
                  </select>
                  <input type="number" step="any" placeholder="Amount (BDT)" value={newAmount} onChange={(e) => setNewAmount(e.target.value)} />
                  <input type="text" placeholder="Counterparty" value={newCounterparty} onChange={(e) => setNewCounterparty(e.target.value)} />
                  <select value={newStatus} onChange={(e) => setNewStatus(e.target.value)}>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="pending">Pending</option>
                    <option value="reversed">Reversed</option>
                  </select>
                  <button type="button" className="add-txn-btn" onClick={handleAddTxn}>Add</button>
                </div>
              </div>
            </div>

            <button type="submit" className="submit-btn" disabled={loading}>
              {loading ? "Analyzing Case Data..." : "Run Investigation ➔"}
            </button>
          </form>
        </section>

        {/* Results Console */}
        <section className="results-section">
          <h2>Investigator Output</h2>
          
          {loading && (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Evaluating evidence & routing queues...</p>
            </div>
          )}

          {error && (
            <div className="error-state">
              <h4>API Execution Error</h4>
              <p>{error}</p>
            </div>
          )}

          {!loading && !error && !response && (
            <div className="empty-state">
              <span className="empty-icon">🔍</span>
              <p>Configure ticket parameters and run analysis to view structured output.</p>
            </div>
          )}

          {response && (
            <div className="response-data">
              <div className="result-cards">
                <div className={`card verdict-${response.evidence_verdict}`}>
                  <div className="card-label">Evidence Verdict</div>
                  <div className="card-value">{response.evidence_verdict.toUpperCase()}</div>
                </div>
                <div className="card">
                  <div className="card-label">Case Type</div>
                  <div className="card-value font-code">{response.case_type}</div>
                </div>
                <div className={`card severity-${response.severity}`}>
                  <div className="card-label">Severity</div>
                  <div className="card-value">{response.severity.toUpperCase()}</div>
                </div>
                <div className="card">
                  <div className="card-label">Assigned Queue</div>
                  <div className="card-value font-code">{response.department}</div>
                </div>
              </div>

              <div className="result-metadata-row">
                <div className="meta-item">
                  <span className="meta-label">Relevant Txn ID:</span>
                  <span className="meta-val font-code">{response.relevant_transaction_id || "None / Null"}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Confidence Score:</span>
                  <span className="meta-val">{(response.confidence * 100).toFixed(0)}%</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Escalation Status:</span>
                  <span className={`escalation-badge review-${response.human_review_required}`}>
                    {response.human_review_required ? "⚠️ ESCALATED FOR HUMAN REVIEW" : "✓ AUTO ROUTED"}
                  </span>
                </div>
              </div>

              <div className="text-output-field">
                <h4>Agent Summary</h4>
                <div className="text-box">{response.agent_summary}</div>
              </div>

              <div className="text-output-field">
                <h4>Recommended Next Action</h4>
                <div className="text-box">{response.recommended_next_action}</div>
              </div>

              <div className="text-output-field">
                <div className="field-header">
                  <h4>Official Customer Reply</h4>
                  <button type="button" className="copy-btn" onClick={handleCopyReply}>
                    {copied ? "Copied!" : "📋 Copy Reply"}
                  </button>
                </div>
                <div className="text-box reply-box">{response.customer_reply}</div>
              </div>

              <div className="raw-json-panel">
                <h4>Raw JSON Response Payload</h4>
                <pre><code>{JSON.stringify(response, null, 2)}</code></pre>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
