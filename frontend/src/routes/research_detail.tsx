// routes/research_detail.tsx — NUR noch ein Redirect (User-Wunsch: alles im
// Chat + Sidepanel statt separater Detail-Seite). Alte Links/Bookmarks
// landen automatisch im Chat mit geöffnetem Panel (?focus=…).
import { Navigate, useParams } from "react-router-dom";

export function ResearchDetailPage() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/research?focus=${id ?? ""}`} replace />;
}
