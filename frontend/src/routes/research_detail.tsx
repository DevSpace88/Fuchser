// routes/research_detail.tsx — now ONLY a redirect (user request: everything
// in the chat + side panel instead of a separate detail page). Old
// links/bookmarks land in the chat with the panel open (?focus=…).
import { Navigate, useParams } from "react-router-dom";

export function ResearchDetailPage() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/research?focus=${id ?? ""}`} replace />;
}
