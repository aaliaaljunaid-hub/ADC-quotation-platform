import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAccount } from "../context/AccountContext";
import { api } from "../api";
import type { Project, QuoteOut, Room } from "../types";

export default function ProjectDetail() {
  const { account } = useAccount();
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [quotes, setQuotes] = useState<QuoteOut[]>([]);
  const [loading, setLoading] = useState(true);

  const [roomForm, setRoomForm] = useState({ floor_name: "", room_name: "", area_sqm: "" });
  const [creatingQuote, setCreatingQuote] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([api.getProject(projectId), api.getRooms(projectId), api.getQuotesForProject(projectId)]).then(
      ([p, r, q]) => {
        setProject(p);
        setRooms(r);
        setQuotes(q);
        setLoading(false);
      }
    );
  }, [projectId]);

  async function handleAddRoom(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId || !roomForm.floor_name || !roomForm.room_name) return;
    const room = await api.createRoom(projectId, {
      floor_name: roomForm.floor_name,
      room_name: roomForm.room_name,
      area_sqm: roomForm.area_sqm || null,
    });
    setRooms([...rooms, room]);
    setRoomForm({ floor_name: "", room_name: "", area_sqm: "" });
  }

  async function handleCreateQuote() {
    if (!account || !project) return;
    setCreatingQuote(true);
    try {
      const quote = await api.createQuote(account.id, {
        project_id: project.id,
        client_name: project.client_name,
      });
      navigate(`/quotes/${quote.id}`);
    } finally {
      setCreatingQuote(false);
    }
  }

  if (loading || !project) return <p>Loading project...</p>;

  return (
    <div>
      <p><Link to="/projects">&larr; All projects</Link></p>
      <h2>{project.project_name}</h2>
      <p className="muted">
        Client: {project.client_name} {project.project_number && `· No. ${project.project_number}`}{" "}
        {project.location && `· ${project.location}`}
      </p>

      <section>
        <h3>Quotes</h3>
        <button onClick={handleCreateQuote} disabled={creatingQuote}>
          {creatingQuote ? "Creating..." : "+ New quote"}
        </button>
        <table className="data-table">
          <thead>
            <tr><th>Quote No.</th><th>Status</th><th>Created</th></tr>
          </thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.id}>
                <td><Link to={`/quotes/${q.id}`}>{q.quote_number || q.id.slice(0, 8)}</Link></td>
                <td>{q.status}</td>
                <td>{new Date(q.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {quotes.length === 0 && <tr><td colSpan={3} className="empty-row">No quotes yet.</td></tr>}
          </tbody>
        </table>
      </section>

      <section>
        <h3>Rooms</h3>
        <form className="inline-form" onSubmit={handleAddRoom}>
          <input
            placeholder="Floor"
            value={roomForm.floor_name}
            onChange={(e) => setRoomForm({ ...roomForm, floor_name: e.target.value })}
            required
          />
          <input
            placeholder="Room name"
            value={roomForm.room_name}
            onChange={(e) => setRoomForm({ ...roomForm, room_name: e.target.value })}
            required
          />
          <input
            placeholder="Area (sqm)"
            type="number"
            step="0.01"
            value={roomForm.area_sqm}
            onChange={(e) => setRoomForm({ ...roomForm, area_sqm: e.target.value })}
            style={{ width: 110 }}
          />
          <button type="submit">Add room</button>
        </form>
        <table className="data-table">
          <thead>
            <tr><th>Floor</th><th>Room</th><th>Tag</th><th>Area (sqm)</th></tr>
          </thead>
          <tbody>
            {rooms.map((r) => (
              <tr key={r.id}>
                <td>{r.floor_name}</td>
                <td>{r.room_name}</td>
                <td>{r.room_tag || "-"}</td>
                <td>{r.area_sqm ?? "-"}</td>
              </tr>
            ))}
            {rooms.length === 0 && <tr><td colSpan={4} className="empty-row">No rooms yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}
