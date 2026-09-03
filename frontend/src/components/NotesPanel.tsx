import { useEffect, useState } from "react";
import { api } from "../api";
import type { Note, Position } from "../types";

interface Props {
  position: Position | null;
}

export function NotesPanel({ position }: Props) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!position) {
      setNotes([]);
      return;
    }
    api.listNotes(position.id).then(setNotes).catch(() => setNotes([]));
  }, [position]);

  if (!position) {
    return (
      <aside className="card notes">
        <h2>Research notes</h2>
        <p className="muted">Select a position to view and add notes.</p>
      </aside>
    );
  }

  async function addNote(e: React.FormEvent) {
    e.preventDefault();
    if (!position || !title.trim()) return;
    setBusy(true);
    try {
      const note = await api.createNote(position.id, { title, content });
      setNotes((prev) => [note, ...prev]);
      setTitle("");
      setContent("");
    } finally {
      setBusy(false);
    }
  }

  async function removeNote(id: number) {
    await api.deleteNote(id);
    setNotes((prev) => prev.filter((n) => n.id !== id));
  }

  return (
    <aside className="card notes">
      <h2>
        Notes · <span className="symbol">{position.symbol}</span>
      </h2>
      {position.thesis && <p className="thesis">{position.thesis}</p>}
      <form className="note-form" onSubmit={addNote}>
        <input
          aria-label="note title"
          placeholder="Note title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          aria-label="note content"
          placeholder="Details, catalysts, risks..."
          value={content}
          rows={3}
          onChange={(e) => setContent(e.target.value)}
        />
        <button type="submit" disabled={busy || !title.trim()}>
          {busy ? "Saving..." : "Add note"}
        </button>
      </form>
      <ul className="note-list">
        {notes.length === 0 && <li className="muted">No notes yet.</li>}
        {notes.map((n) => (
          <li key={n.id} className="note-item">
            <div className="note-head">
              <strong>{n.title}</strong>
              <button
                className="link-danger"
                aria-label={`delete note ${n.title}`}
                onClick={() => removeNote(n.id)}
              >
                ✕
              </button>
            </div>
            {n.content && <p>{n.content}</p>}
            <time>{new Date(n.created_at).toLocaleString()}</time>
          </li>
        ))}
      </ul>
    </aside>
  );
}
