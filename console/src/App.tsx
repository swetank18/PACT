/**
 * Three surfaces, not one dashboard. LANE-C preamble.
 *
 * Grant, checkout and merchant console are separate places with separate jobs,
 * and the router is a hash router with no dependency because a demo laptop that
 * cannot resolve a route is a stage failure and static hosting is one less
 * moving part.
 */
import { useCallback, useEffect, useState } from "react";

import { DemoBar } from "./DemoBar";
import { StepUpModal } from "./components/StepUpModal";
import { checkParity, type ParityState } from "./lib/parity";
import { LiveDataProvider, useLive } from "./lib/store";
import { shortId } from "./lib/money";
import { Checkout } from "./surfaces/checkout/Checkout";
import { Console } from "./surfaces/console/Console";
import { Firewall } from "./surfaces/firewall/Firewall";
import { Grant, type GrantResult } from "./surfaces/grant/Grant";
import { Slides } from "./surfaces/slides/Slides";
import s from "./App.module.css";

type Route = "grant" | "checkout" | "console" | "slides" | "firewall";

const ROUTES: Array<{ id: Route; label: string }> = [
  { id: "grant", label: "Grant" },
  { id: "checkout", label: "Checkout" },
  { id: "console", label: "Merchant console" },
  { id: "firewall", label: "Firewall" },
  { id: "slides", label: "Pitch" },
];

function useHashRoute(): [Route, (r: Route) => void] {
  // Only the first segment names the surface. The firewall keeps its own tab
  // in the second one, so #/firewall/mandates still routes here.
  const read = (): Route => {
    const raw = window.location.hash.replace(/^#\/?/, "").split("/")[0];
    return (ROUTES.find((r) => r.id === raw)?.id ?? "console") as Route;
  };
  const [route, setRoute] = useState<Route>(read);

  useEffect(() => {
    const onHash = () => setRoute(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return [route, (r: Route) => (window.location.hash = `#/${r}`)];
}

/**
 * The blocking gate, on screen. A judge can point at this and ask what it
 * means, and the answer is short: the signature this browser produces is
 * byte-identical to the one the Python engine produces from the same key and
 * the same object.
 */
function ParityBadge() {
  const [state, setState] = useState<ParityState>({ status: "checking" });

  useEffect(() => {
    void checkParity().then(setState);
  }, []);

  if (state.status === "unavailable") return null;

  const cls =
    state.status === "ok" ? s.parityOk : state.status === "failed" ? s.parityFailed : "";
  const text =
    state.status === "ok"
      ? `signature parity · ${state.cases} vectors`
      : state.status === "failed"
        ? "signature parity FAILED"
        : "checking parity";

  return (
    <span
      className={`${s.parity} ${cls}`}
      title={
        state.status === "failed"
          ? state.detail
          : "Ed25519 over RFC 8785 JCS, verified in this browser against the cross language test vector"
      }
    >
      {text}
    </span>
  );
}

function Shell() {
  const [route, go] = useHashRoute();
  const [grant, setGrant] = useState<GrantResult | null>(null);
  const { pendingStepUp, clearStepUp, loadSaga, saga, orders, refetchAll } = useLive();

  const onGranted = useCallback(
    (r: GrantResult) => {
      setGrant(r);
      go("checkout");
    },
    [go],
  );

  // The firewall is the principal's own product, not a panel in the merchant
  // console: it takes the viewport, brings its own light theme and its own
  // navigation, and handles a step up in place rather than through the shell's
  // modal. The demo strip stays, because it is the only way to put traffic on
  // the screen without typing on stage.
  if (route === "firewall") {
    return (
      <div className={s.appFull}>
        <main className={s.fullMain}>
          <Firewall onExit={() => go("console")} />
        </main>
        <DemoBar />
      </div>
    );
  }

  return (
    <div className={s.app}>
      <header className={s.bar}>
        <div className={s.brand}>
          <span className={s.wordmark}>PACT</span>
          <span className={s.tagline}>the merchant reads the buyer's authority before it quotes</span>
        </div>

        <ParityBadge />

        {grant && <span className={s.mandateChip}>{shortId(grant.mandate.mandate_id, 8)}</span>}

        <nav className={s.nav}>
          {ROUTES.map((r) => (
            <button
              key={r.id}
              className={`${s.tab} ${route === r.id ? s.tabOn : ""}`}
              onClick={() => go(r.id)}
            >
              {r.label}
            </button>
          ))}
        </nav>
      </header>

      <main className={s.main}>
        {route === "grant" && <Grant onGranted={onGranted} />}
        {route === "checkout" && (
          <Checkout
            grant={grant}
            saga={saga}
            orders={orders}
            onWatchOrder={(id) => void loadSaga(id)}
          />
        )}
        {route === "console" && <Console />}
        {route === "slides" && <Slides />}
      </main>

      <DemoBar />

      {/* A step up can arrive while the operator is on any surface, so the modal
          lives at the shell rather than inside checkout. */}
      {pendingStepUp && (
        <StepUpModal
          decision={pendingStepUp}
          onResolved={() => {
            clearStepUp();
            void refetchAll();
          }}
        />
      )}
    </div>
  );
}

export function App() {
  return (
    <LiveDataProvider>
      <Shell />
    </LiveDataProvider>
  );
}
