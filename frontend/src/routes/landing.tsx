// routes/landing.tsx — Die Landingpage für ausgeloggte Besucher.
// ============================================================================
// Präsentiert Fuchser: Hero mit modernem Mesh/Glow-Effekt, interaktivem
// Live-Demo-Kartenwechsler (simuliert autonome Agenten-Recherche),
// Feature-Grid und detaillierter LangGraph-Pipeline.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { FoxIcon } from "@/components/FoxIcon";

import {
  Brain,
  Globe,
  FileText,
  ShieldCheck,
  ArrowRight,
  RefreshCw,
  Users,
  Zap,
  BookOpen,
  FileDown,
  Lock,
  Sparkles,
  Check,
} from "lucide-react";

// ----------------------------------------------------------------------------
// 1) LIVE-DEMO DATA: Verschiedene interaktive Beispiel-Recherchen
// ----------------------------------------------------------------------------
interface DemoItem {
  id: string;
  tabLabel: string;
  question: string;
  subQuestions: string[];
  answer: string;
  sources: string[];
}

const DEMO_ITEMS: DemoItem[] = [
  {
    id: "balkonkraftwerk",
    tabLabel: "⚡ Balkonkraftwerk 2026",
    question: "Lohnt sich ein Balkonkraftwerk 2026 in Deutschland?",
    subQuestions: ["Anschaffungskosten & Amortisation", "Rechtliches & 800W-Regel", "Stromersparnis & Speicher"],
    answer:
      "Ein Balkonkraftwerk amortisiert sich 2026 typischerweise in 4–7 Jahren. Bis zu 800 W Einspeiseleistung sind bundesweit erlaubt [1], und die Registrierung im Marktstammdatenregister ist auf wenige Klicks reduziert [2]. Bei durchschnittlicher Nutzung sparen Haushalte ca. 150–250 € Stromkosten pro Jahr [3].",
    sources: ["BDEW", "Verbraucherzentrale", "Bundesnetzagentur"],
  },
  {
    id: "akkus",
    tabLabel: "🔋 Feststoffbatterien",
    question: "Wann kommen Feststoffbatterien für E-Autos in Serie?",
    subQuestions: ["Serienstart & Automobilhersteller", "Energiedichte & Ladezeiten", "Kosten & Skalierbarkeit"],
    answer:
      "Erste Pilotserien mit Feststoffakkus (Solid-State) starten zwischen 2026 und 2028 bei Herstellern wie Toyota und QuantumScape [1]. Die Energiedichte steigt um ca. 40–60 % gegenüber NMC-Zellen [2], während die Ladezeit auf unter 12 Minuten sinken soll [3].",
    sources: ["Fraunhofer ISI", "Nature Energy", "VDA"],
  },
  {
    id: "llms",
    tabLabel: "🤖 Lokale LLMs",
    question: "Wie betreibt man DeepSeek & LLaMA 3 lokal auf Mac/PC?",
    subQuestions: ["Hardware-Voraussetzungen (VRAM)", "Ollama & vLLM Setup", "Quantisierung (Q4/Q8)"],
    answer:
      "Für lokale LLMs bis 14B Parametern reichen 16–24 GB Unified Memory (Apple Silicon) oder eine RTX 4070 [1]. Mit Frameworks wie Ollama oder llama.cpp genügen 4-Bit-Quantisierungen (GGUF), um bei < 2 % Qualitätsverlust 35+ Tokens/Sekunde zu erreichen [2].",
    sources: ["Hugging Face", "Ollama Docs", "ArXiv 2401.x"],
  },
];

function useTypewriter(text: string, speed = 20, startDelay = 400) {
  const [output, setOutput] = useState("");
  useEffect(() => {
    setOutput("");
    let timeout: NodeJS.Timeout;
    let interval: NodeJS.Timeout;

    timeout = setTimeout(() => {
      let i = 0;
      interval = setInterval(() => {
        i += 1;
        setOutput(text.slice(0, i));
        if (i >= text.length) clearInterval(interval);
      }, speed);
    }, startDelay);

    return () => {
      clearTimeout(timeout);
      clearInterval(interval);
    };
  }, [text, speed, startDelay]);

  return output;
}

function DemoCard() {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const currentDemo = DEMO_ITEMS[selectedIdx];

  const [phase, setPhase] = useState(0); // 0=planen, 1=recherchieren, 2=antwort
  const typed = useTypewriter(phase === 2 ? currentDemo.answer : "");

  // Wenn der Tab gewechselt wird, starte die Phase von vorne
  const handleSelectTab = (idx: number) => {
    if (idx === selectedIdx) return;
    setSelectedIdx(idx);
    setPhase(0);
  };

  // Phasen-Loop: planen (1.4s) → recherchieren (2.0s) → tippen → Pause → von vorn
  useEffect(() => {
    if (phase === 0) {
      const t = setTimeout(() => setPhase(1), 1400);
      return () => clearTimeout(t);
    }
    if (phase === 1) {
      const t = setTimeout(() => setPhase(2), 2000);
      return () => clearTimeout(t);
    }
    if (phase === 2 && typed === currentDemo.answer) {
      const t = setTimeout(() => setPhase(0), 6000);
      return () => clearTimeout(t);
    }
  }, [phase, typed, currentDemo.answer]);

  return (
    <div className="rounded-2xl border border-border/80 bg-card/95 p-3.5 sm:p-5 text-left shadow-2xl backdrop-blur-md transition-all duration-300">
      {/* Header & Status */}
      <div className="mb-4 flex items-center justify-between border-b border-border/40 pb-3">
        <div className="flex items-center gap-2">
          <FoxIcon size={18} className="text-primary" />
          <span className="font-semibold text-xs tracking-tight text-foreground">Recherche-Vorschau</span>
        </div>

        <div className="flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-medium text-primary">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
          </span>
          <span>Live-Demo aktiv</span>
        </div>
      </div>

      {/* Interaktive Prompt-Tabs */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {DEMO_ITEMS.map((item, idx) => (
          <button
            key={item.id}
            onClick={() => handleSelectTab(idx)}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-all ${
              selectedIdx === idx
                ? "bg-primary text-primary-foreground shadow-sm"
                : "bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {item.tabLabel}
          </button>
        ))}
      </div>

      {/* Frage */}
      <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
        <div className="flex items-start gap-2">
          <span className="rounded bg-primary/15 px-2 py-0.5 font-mono text-[11px] font-semibold text-primary">
            PROMPT
          </span>
          <p className="text-sm font-medium text-foreground">{currentDemo.question}</p>
        </div>
      </div>

      {/* Sub-Agenten-Pipeline */}
      <div className="mt-3.5 grid gap-2 sm:grid-cols-3">
        {currentDemo.subQuestions.map((q, idx) => {
          const done = phase > 1;
          const active = phase === 1;
          return (
            <div
              key={q}
              className={
                "flex items-center gap-2 rounded-xl border p-2.5 text-xs transition-all duration-500 " +
                (done
                  ? "border-primary/40 bg-primary/5 text-foreground"
                  : active
                    ? "animate-pulse border-secondary/60 bg-secondary/5 text-foreground shadow-sm"
                    : "opacity-40 text-muted-foreground")
              }
            >
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-background font-mono text-[10px] font-bold">
                {done ? "✓" : active ? "🔎" : `0${idx + 1}`}
              </span>
              <span className="truncate">{q}</span>
            </div>
          );
        })}
      </div>

      {/* Antwort-Streaming: Fester Bereich, der den vollständigen Text von Anfang an aufnimmt */}
      <div className="mt-4 min-h-[8.5rem] sm:min-h-[7rem] rounded-xl border border-border/50 bg-muted/60 p-4 text-sm leading-relaxed text-foreground">
        {phase === 0 && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Brain className="h-4 w-4 animate-pulse text-primary" />
            <span className="animate-pulse">Supervisor analysiert und zerlegt die Fragestellung …</span>
          </div>
        )}
        {phase === 1 && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Globe className="h-4 w-4 animate-spin text-secondary" />
            <span className="animate-pulse">3 Sub-Researcher durchsuchen das Web parallel …</span>
          </div>
        )}
        {phase === 2 && (
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-primary">
              <Sparkles className="h-3.5 w-3.5" />
              <span>Synthese-Bericht</span>
            </div>
            {typed}
            {typed.length < currentDemo.answer.length && (
              <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />
            )}
          </div>
        )}
      </div>

      {/* Quellen-Chips */}
      <div className="mt-3 flex min-h-[2rem] items-center flex-wrap gap-1.5">
        {(phase === 2 ? currentDemo.sources : []).map((s, i) => (
          <span
            key={s}
            className="flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-0.5 text-xs text-accent"
            style={{ animation: `fade-in 0.4s ease-out ${i * 0.15}s both` }}
          >
            <BookOpen className="h-3 w-3" />
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// 2) FEATURES DATA
// ----------------------------------------------------------------------------
const FEATURES = [
  {
    icon: Users,
    color: "text-primary bg-primary/10 border-primary/20",
    title: "Multi-Agent-Power",
    text: "Ein Supervisor zerlegt deine Frage, parallele Researcher graben durch das Web, ein Synthesizer schreibt den Report.",
  },
  {
    icon: RefreshCw,
    color: "text-secondary bg-secondary/10 border-secondary/20",
    title: "Kritiker-Qualitätsloop",
    text: "Ein Kritiker prüft jeden Report auf Lücken — und lässt bei Bedarf automatisch nochmal gezielt recherchieren.",
  },
  {
    icon: Zap,
    color: "text-accent bg-accent/10 border-accent/20",
    title: "Alles live gestreamt",
    text: "Du siehst in Echtzeit per SSE, welcher Agent gerade arbeitet — vom ersten Plan bis zur letzten Zeile des Reports.",
  },
  {
    icon: BookOpen,
    color: "text-chart-3 bg-chart-3/10 border-chart-3/20",
    title: "Echte, zitierte Quellen",
    text: "Jede Aussage ist mit [1], [2] belegt — alle Quellen klickbar und verifizierbar. Kein Halluzinieren im Dunkeln.",
  },
  {
    icon: FileDown,
    color: "text-chart-4 bg-chart-4/10 border-chart-4/20",
    title: "Sauberer PDF-Export",
    text: "Den fertigen Report inklusive Quellenverzeichnis und Metadaten als saubere PDF-Datei herunterladen.",
  },
  {
    icon: Lock,
    color: "text-chart-5 bg-chart-5/10 border-chart-5/20",
    title: "Privat & Sicher",
    text: "Login mit JWT, jedes Recherche-Projekt gehört strikt dir — niemand sonst hat Zugriff auf deine Daten.",
  },
];

// ----------------------------------------------------------------------------
// 3) PIPELINE DATA
// ----------------------------------------------------------------------------
const PIPELINE = [
  {
    step: "1",
    icon: Brain,
    color: "text-primary bg-primary/10 border-primary/20",
    name: "Supervisor",
    text: "Plant und strukturiert 2–4 recherchierbare Teilfragen für die Tiefenrecherche.",
  },
  {
    step: "2",
    icon: Globe,
    color: "text-secondary bg-secondary/10 border-secondary/20",
    name: "Researcher",
    text: "Suchen parallel im Web, extrahieren Fakten und sammeln verifizierbare Quellen.",
  },
  {
    step: "3",
    icon: FileText,
    color: "text-accent bg-accent/10 border-accent/20",
    name: "Synthesizer",
    text: "Verdichtet alle Erkenntnisse zu einem strukturierten Report mit direkten Zitaten.",
  },
  {
    step: "4",
    icon: ShieldCheck,
    color: "text-chart-4 bg-chart-4/10 border-chart-4/20",
    name: "Kritiker",
    text: "Prüft auf Lücken & Fakten — und schickt bei Bedarf eine Nachrecherche los.",
    hasLoop: true,
  },
];

export function LandingPage() {
  const { user } = useAuth();

  return (
    <div className="min-h-screen overflow-x-hidden">
      {/* ============ HERO ============ */}
      <section className="relative mx-auto max-w-6xl px-4 pt-8 pb-16 text-center sm:px-6 sm:pt-20 sm:pb-32">
        {/* Glow & Mesh-Hintergrund */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[32rem] blur-3xl opacity-70"
          style={{
            background:
              "radial-gradient(ellipse 65% 55% at 50% 0%, rgb(var(--primary) / 0.22), transparent 70%)",
          }}
        />
        <div className="pointer-events-none absolute -top-16 left-1/2 -z-10 -translate-x-1/2 text-[10rem] opacity-10 select-none sm:text-[16rem]">
          🦊
        </div>

        <h1 className="mt-2 sm:mt-4 text-3xl font-bold tracking-tight sm:text-6xl text-balance">
          Deine Recherche.
          <br />
          <span
            className="bg-clip-text text-transparent"
            style={{
              backgroundImage:
                "linear-gradient(90deg, rgb(var(--primary)), rgb(var(--secondary)), rgb(var(--accent)))",
            }}
          >
            Tief gegraben.
          </span>
        </h1>

        <p className="mx-auto mt-5 max-w-xl text-balance text-muted-foreground text-pretty sm:text-lg leading-relaxed">
          Frag Fuchser irgendwas — ein Schwarm aus spezialisierten KI-Agenten durchsucht das
          Web, prüft Ergebnisse gegenseitig und schreibt dir einen fertigen Report
          mit echten Quellen. Live dabei zusehen inklusive.
        </p>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          {user ? (
            <Button asChild size="lg" className="shadow-lg hover:shadow-primary/25 transition-all">
              <Link to="/research">
                <Globe className="mr-2 h-4 w-4" /> Zur Recherche
              </Link>
            </Button>
          ) : (
            <>
              <Button asChild size="lg" className="shadow-lg hover:shadow-primary/25 transition-all">
                <Link to="/register">
                  <Sparkles className="mr-2 h-4 w-4" /> Kostenlos starten
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className="backdrop-blur-sm">
                <Link to="/login">Anmelden</Link>
              </Button>
            </>
          )}
        </div>

        {/* Vertrauens-Punkte */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-primary" /> Keine Kreditkarte nötig
          </span>
          <span className="flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-primary" /> Echte, verifizierte Quellen
          </span>
          <span className="flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-primary" /> Live SSE-Streaming
          </span>
        </div>

        {/* Live-Demo */}
        <div className="mx-auto mt-12 max-w-2xl">
          <DemoCard />
        </div>
      </section>

      {/* ============ FEATURES ============ */}
      <section className="border-t bg-card/50 py-24">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl text-balance">
              Warum <span className="text-primary">Fuchser</span>?
            </h2>
            <p className="mx-auto mt-3 max-w-lg text-center text-sm text-muted-foreground text-pretty">
              Entwickelt für fundierte Tiefenrecherche statt oberflächlicher Standard-Chatbots.
            </p>
          </div>

          <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => {
              const Icon = f.icon;
              return (
                <div
                  key={f.title}
                  className="rounded-2xl border bg-card p-6 transition-all duration-200 hover:-translate-y-1 hover:border-primary/40 hover:shadow-lg"
                >
                  <div className={`inline-flex h-11 w-11 items-center justify-center rounded-xl border ${f.color}`}>
                    <Icon className="h-5 w-5" />
                  </div>
                  <h3 className="mt-4 font-semibold text-foreground">{f.title}</h3>
                  <p className="mt-2 text-sm text-muted-foreground text-pretty leading-relaxed">{f.text}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ============ PIPELINE ============ */}
      <section className="py-24">
        <div className="mx-auto max-w-5xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl text-balance">Unter der Haube</h2>
            <p className="mx-auto mt-3 max-w-xl text-balance text-sm text-muted-foreground text-pretty">
              Vier spezialisierte Agenten in einem LangGraph-Zustandsnetzwerk. Jeder Zwischenschritt
              wird persistent gespeichert und live in deinen Browser gestreamt.
            </p>
          </div>

          <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE.map((p, i) => {
              const Icon = p.icon;
              return (
                <div
                  key={p.name}
                  className="relative flex flex-col justify-between rounded-2xl border bg-card p-6 shadow-sm transition-all duration-200 hover:border-primary/40 hover:shadow-md"
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <div className={`inline-flex h-11 w-11 items-center justify-center rounded-xl border ${p.color}`}>
                        <Icon className="h-5 w-5" />
                      </div>
                      <span className="font-mono text-xs font-semibold text-muted-foreground">
                        SCHRITT {p.step}
                      </span>
                    </div>

                    <h3 className="mt-4 text-base font-semibold text-foreground">{p.name}</h3>
                    <p className="mt-1.5 text-xs text-muted-foreground text-pretty leading-relaxed">{p.text}</p>
                  </div>

                  {p.hasLoop && (
                    <div className="mt-4 flex items-center gap-1.5 rounded-md bg-secondary/10 px-2.5 py-1 text-[11px] font-medium text-secondary">
                      <RefreshCw className="h-3 w-3" />
                      <span>Loop bei Lücken</span>
                    </div>
                  )}

                  {/* Eleganter Pfeil zum nächsten Schritt (Desktop) */}
                  {i < PIPELINE.length - 1 && (
                    <div className="pointer-events-none absolute -right-3 top-1/2 -translate-y-1/2 z-10 hidden lg:flex h-6 w-6 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm">
                      <ArrowRight className="h-3 w-3" />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ============ CTA ============ */}
      <section className="relative overflow-hidden border-t bg-card/50 py-24 text-center">
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-64 blur-3xl opacity-40"
          style={{
            background:
              "radial-gradient(ellipse 60% 50% at 50% 100%, rgb(var(--primary) / 0.25), transparent 70%)",
          }}
        />
        <div className="mx-auto max-w-2xl px-6">
          <FoxIcon size={68} className="mx-auto text-primary drop-shadow-xl" />
          <h2 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl text-balance">
            Fuchs dich rein. <span className="text-primary">Wortwörtlich.</span>
          </h2>
          <p className="mt-3 text-muted-foreground text-pretty sm:text-base">
            Die erste Recherche dauert keine zwei Minuten. Starte jetzt kostenlos.
          </p>
          <Button asChild size="lg" className="mt-8 shadow-xl hover:shadow-primary/25 transition-all">
            <Link to={user ? "/research?new=1" : "/register"}>
              {user ? "🔍 Neue Recherche starten" : "Jetzt kostenlos starten"}
            </Link>
          </Button>
        </div>
      </section>

      <footer className="border-t py-8 text-center text-xs text-muted-foreground">
        Fuchser — Multi-Agent Deep Research · FastAPI + LangGraph + React
      </footer>
    </div>
  );
}

