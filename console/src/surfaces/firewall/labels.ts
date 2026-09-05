/**
 * Turning a decision into words a person recognises.
 *
 * A decision names a mandate, not an agent, and the mandate body only exists
 * on the device that signed it. So the honest answer for anything signed
 * elsewhere — the simulation harness, another browser — is the mandate id
 * rather than a guessed agent name.
 */
import { useMemo } from "react";

import { shortId } from "../../lib/money";
import { useFirewall } from "./provider";
import type { StoredMandate } from "./state";

export type Labels = {
  mandateFor: (mandateId: string) => StoredMandate | undefined;
  agentLabel: (mandateId: string) => string;
  /** True when the label is a fallback, so the UI can style it as unknown. */
  isKnown: (mandateId: string) => boolean;
  intentFor: (mandateId: string) => string | undefined;
};

export function useLabels(): Labels {
  const { mandates, agents } = useFirewall();

  return useMemo(() => {
    const byId = new Map(mandates.map((m) => [m.mandate.mandate_id, m]));
    const agentName = new Map(agents.map((a) => [a.agent_id, a.name]));

    const mandateFor = (id: string) => byId.get(id);

    return {
      mandateFor,
      isKnown: (id: string) => byId.has(id),
      agentLabel: (id: string) => {
        const sm = byId.get(id);
        if (!sm) return shortId(id, 6);
        return agentName.get(sm.mandate.delegate.agent_id) ?? sm.mandate.delegate.agent_id;
      },
      intentFor: (id: string) => byId.get(id)?.mandate.intent,
    };
  }, [mandates, agents]);
}
