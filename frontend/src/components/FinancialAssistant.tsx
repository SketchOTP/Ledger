import { useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { Btn, Card } from "./ui";

type Turn = { role: "user" | "assistant"; content: string };

export default function FinancialAssistant() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);

  const ask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    const history = turns.slice(-12);
    const requestGeneration = generation.current;
    setQuestion("");
    setTurns((current) => [...current, { role: "user", content: text }]);
    setBusy(true);
    setError("");
    try {
      const reply = await api.askFinancialAssistant(text, history);
      if (requestGeneration === generation.current) {
        setTurns((current) => [...current, { role: "assistant", content: reply.answer }]);
      }
    } catch (err) {
      if (requestGeneration === generation.current) {
        setError(err instanceof Error ? err.message : "The assistant could not answer. Try again.");
      }
    } finally {
      if (requestGeneration === generation.current) setBusy(false);
    }
  };

  return <Card className="financial-assistant-card">
    <div className="section-head"><div><h3>Ask about your money</h3><p className="subtitle">Ask about due dates, the math, or a what-if. Answers use your recorded plan; nothing changes automatically.</p></div><Btn variant="secondary" onClick={() => { generation.current += 1; setTurns([]); setQuestion(""); setError(""); setBusy(false); }}>New</Btn></div>
    {turns.length > 0 && <div className="financial-assistant-messages" aria-live="polite">{turns.map((turn, index) => <div className={`financial-assistant-message ${turn.role}`} key={`${index}-${turn.role}`}><strong>{turn.role === "user" ? "You" : "Ledger"}</strong><p>{turn.content}</p></div>)}</div>}
    {error && <p className="error" role="alert">{error}</p>}
    <form onSubmit={(event) => void ask(event)} className="financial-assistant-form"><label htmlFor="financial-question">Your question</label><textarea id="financial-question" className="input" value={question} maxLength={2000} rows={3} onChange={(event) => setQuestion(event.target.value)} placeholder="Can I buy groceries on Saturday and still cover the mortgage?" required /><Btn type="submit" disabled={busy || !question.trim()}>{busy ? "Thinking…" : "Ask"}</Btn></form>
    <p className="field-hint">This conversation stays on this page only. New clears it; refreshing the page also clears it. Check any proposed change with the payee before relying on it.</p>
  </Card>;
}
