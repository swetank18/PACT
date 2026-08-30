/**
 * The revenue strip. LANE-C section 4.
 *
 *   GMV TODAY   AVG ORDER   UPSELL ATTACH   RECOVERED
 *
 * RECOVERED is revenue from sales that failed and came back through the
 * alternative offer. It is the tile to point at, so it is the only one that
 * carries colour.
 */
import type { MerchantStats } from "../lib/contracts";
import { inrPlain, pct } from "../lib/money";
import s from "./RevenueStrip.module.css";

function Tile({
  label,
  value,
  symbol,
  sub,
  variant,
}: {
  label: string;
  value: string;
  symbol?: string;
  sub?: string;
  variant?: "recovered" | "attention";
}) {
  return (
    <div className={`${s.tile} ${variant ? s[variant] : ""}`}>
      <div className={s.label}>{label}</div>
      <div className={s.value}>
        {symbol && <span className={s.symbol}>{symbol}</span>}
        {value}
      </div>
      <div className={s.sub}>{sub}</div>
    </div>
  );
}

export function RevenueStrip({ stats }: { stats: MerchantStats }) {
  const { upsell_offers_made: made, upsell_offers_accepted: accepted } = stats;

  return (
    <div className={s.strip}>
      <Tile
        label="GMV today"
        symbol="₹"
        value={inrPlain(stats.gmv_paise)}
        sub={`${stats.orders} order${stats.orders === 1 ? "" : "s"} settled`}
      />
      <Tile
        label="Avg order"
        symbol="₹"
        value={inrPlain(stats.avg_order_value_paise)}
        sub="headroom upsell shows up here"
      />
      <Tile
        label="Upsell attach"
        value={pct(stats.upsell_attach_rate)}
        sub={
          made > 0
            ? `${accepted} of ${made} offers accepted · ${stats.upsell_offers_filtered_by_headroom} filtered before offering`
            : "no offers made yet"
        }
      />
      <Tile
        label="Recovered"
        symbol="₹"
        value={inrPlain(stats.recovered_paise)}
        variant="recovered"
        sub={`${stats.recovered_orders} sale${
          stats.recovered_orders === 1 ? "" : "s"
        } saved after rollback`}
      />
    </div>
  );
}
