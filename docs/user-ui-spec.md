# Firewall — User UI Specification

> **Product:** Agent Payment Firewall
> **Role:** Principal (Human User)
> **Platform:** Laptop / Desktop only
> **Theme:** Light mode only
> **Philosophy:** Clean, color-coded, enterprise-ready, zero clutter, instant readability

---

## 🎨 Design System

### Color Palette

Every color has a **purpose**. Nothing is decorative without reason.

| Color | Hex | Usage |
|-------|-----|-------|
| **Emerald Green** | `#10B981` | ✅ Allowed verdicts, healthy mandates, success states |
| **Crimson Red** | `#EF4444` | ❌ Blocked verdicts, critical health, kill switch, errors |
| **Amber Gold** | `#F59E0B` | ⚠️ Step-up verdicts, watch health, warnings, pending |
| **Royal Blue** | `#3B82F6` | 🔵 Primary actions, links, active tab, info states |
| **Indigo** | `#6366F1` | 💜 Analytics, charts, accent highlights |
| **Slate 900** | `#0F172A` | Text primary |
| **Slate 500** | `#64748B` | Text secondary, labels |
| **Slate 100** | `#F1F5F9` | Page background |
| **White** | `#FFFFFF` | Card backgrounds |
| **Slate 200** | `#E2E8F0` | Borders, dividers |

### Verdict Color System (used globally across all screens)
```
ALLOWED  →  Green badge (#10B981) + light green bg (#ECFDF5)
BLOCKED  →  Red badge (#EF4444) + light red bg (#FEF2F2)
STEP-UP  →  Amber badge (#F59E0B) + light amber bg (#FFFBEB)
SKIPPED  →  Gray badge (#94A3B8) + light gray bg (#F8FAFC)
```

### Typography
| Element | Font | Weight | Size |
|---------|------|--------|------|
| Page title | Inter | 700 (Bold) | 28px |
| Section heading | Inter | 600 (Semibold) | 20px |
| Card title | Inter | 600 | 16px |
| Body text | Inter | 400 (Regular) | 14px |
| Labels / captions | Inter | 500 (Medium) | 12px |
| Metric numbers | Inter | 700 | 32px |
| Code / JSON | JetBrains Mono | 400 | 13px |

> [!TIP]
> **Import Inter and JetBrains Mono from Google Fonts.** These are the two most used fonts in modern enterprise SaaS products. They signal "professional tool" at a glance.

### Spacing & Layout
- **Page padding:** 32px horizontal, 24px vertical
- **Card padding:** 24px
- **Card border-radius:** 12px
- **Card shadow:** `0 1px 3px rgba(0,0,0,0.08)` (subtle, not heavy)
- **Gap between cards:** 16px
- **Max content width:** 1400px, centered
- **Sidebar width:** 240px, fixed

### Interaction Principles
- **Hover on cards:** Slight lift — `transform: translateY(-2px)` + shadow increase. Transition: `0.2s ease`
- **Hover on buttons:** Background darkens 10%, cursor pointer
- **Hover on table rows:** Row background shifts to `#F8FAFC`
- **Active tab:** Blue left border (4px) + blue text + light blue background (`#EFF6FF`)
- **Transitions:** Everything animated at `0.2s ease` — never instant, never slow
- **Loading states:** Skeleton shimmer (not spinners) — gray pulsing rectangles matching the shape of real content

---

## 🧭 Navigation Structure

**Layout:** Fixed sidebar (left) + main content area (right)

The sidebar has **6 tabs**. Each tab icon + label. Hover highlights with a subtle blue-gray background. Active tab has a blue left accent bar.

```
┌──────────────────────────────────────────────────────┐
│ 🛡️ FIREWALL          [sidebar]  │  [main content]    │
│                                 │                    │
│  📊  Dashboard          ←active │  Page content      │
│  📜  Mandates                   │  goes here         │
│  🔍  Transactions               │                    │
│  📈  Analytics                  │                    │
│  🤖  Agents                     │                    │
│  ⚙️  Settings                   │                    │
│                                 │                    │
│  ──────────────                 │                    │
│  🔴 KILL SWITCH                 │                    │
│  (always visible at bottom)     │                    │
└──────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **The Kill Switch lives in the sidebar, always visible on every page.** It's not buried inside a settings page. It's a persistent red button at the bottom of the navigation. One click → all agents paused. This communicates "you are always in control" no matter what screen you're on.

### Tab Hover Effect
```css
.nav-item {
    padding: 12px 16px;
    border-radius: 8px;
    transition: all 0.2s ease;
    cursor: pointer;
    border-left: 4px solid transparent;
}

.nav-item:hover {
    background: #F1F5F9;       /* subtle gray highlight */
    transform: translateX(4px); /* slight slide right */
}

.nav-item.active {
    background: #EFF6FF;       /* light blue */
    border-left-color: #3B82F6; /* blue accent bar */
    color: #3B82F6;
    font-weight: 600;
}
```

> [!TIP]
> **The sidebar should have the product logo at the top**, a subtle divider before the Kill Switch, and the user's avatar + name at the very bottom (clickable → goes to Settings). Keep it minimal — icon + label only, no sub-menus in the sidebar.

---

## Tab 1: 📊 Dashboard

**Purpose:** Command center. First screen after login. At a glance: "Is everything okay?"
**Max items on screen:** 6

---

### 1.1 Stat Bar (Top Row)

Four metric cards in a horizontal row, full width.

| Card | Content | Color Accent | Icon |
|------|---------|-------------|------|
| **Today's Spend** | ₹2,400 | Blue left border | 💰 |
| **Active Mandates** | 5 | Green left border | 📜 |
| **Pending Step-Ups** | 2 | Amber left border + pulse animation | ⏳ |
| **Blocks Today** | 3 | Red left border | 🛡️ |

**Layout:** 4 cards in a `grid-template-columns: repeat(4, 1fr)` row.

Each card:
- White background, 12px border-radius
- Colored left border (4px)
- Large number (32px, bold) + small label below (12px, gray)
- Icon top-right corner (muted)
- On hover: slight lift

> [!TIP]
> **"Pending Step-Ups" card should pulse gently if count > 0.** A subtle animation (border glows amber every 2s) draws the eye without being obnoxious. This tells the user "something needs your attention" even in peripheral vision.

**Empty state:** Show "₹0" / "0" with muted text. Never hide cards — the layout should be stable.
**Loading state:** Skeleton shimmer in place of numbers.

---

### 1.2 Alerts Banner

Directly below the stat bar. Horizontal banner, only shows if there are alerts. Dismissible (X button).

**Alert types (color-coded):**
- 🔴 **Red alert:** "3 blocked payment attempts in the last hour" → links to Transactions tab
- 🟡 **Amber alert:** "Mandate 'Monthly Groceries' expires in 2 hours" → links to Mandate detail
- 🔵 **Blue alert:** "New agent 'ShopBot v2' connected" → links to Agents tab

**Layout:** Stacked vertically if multiple alerts. Max 3 visible, "View all" link if more.

Each alert:
- Colored left border (matches type)
- Icon + text + timestamp + dismiss button
- Clickable — navigates to relevant page

> [!TIP]
> **Don't show an empty alerts section.** If there are zero alerts, this entire section disappears and the content below moves up. Empty space labeled "No alerts" looks broken.

---

### 1.3 Threat Intelligence Card ⭐ (Custom Suggestion #3)

A standout card below the alerts banner. Slightly larger than stat cards. This is the "wow" card judges read from across the room.

**Content:**
```
🛡️ Threat Summary — Last 7 Days

  ₹12,400  unauthorized spend prevented
  3        prompt injection attempts detected
  1        replay attack blocked
  
  Most targeted category: Gift Cards
  Most active threat window: 2:00 AM – 4:00 AM
```

**Design:**
- Wider card spanning ~60% of the content width
- Left side: big numbers (₹12,400 in large bold green text = "money saved")
- Right side: small detail lines
- Subtle gradient background: white → very light green (`#F0FDF4`) — communicates "protection"
- Small shield icon top-left

**Why this works for judges:** They glance at the dashboard and immediately see a concrete number — "this product prevented ₹12,400 in theft." Value proposition in 2 seconds.

> [!TIP]
> **The "₹12,400 prevented" number should be the largest text on the entire dashboard.** Bigger than the stat bar numbers. This is the single most compelling data point. Make it visually dominant — 40px font, bold, green.

**Empty state:** "No threats detected. Your agents are operating normally. ✅" — still show the card, but with a calming message.

---

### 1.4 Live Activity Feed

Takes up the remaining space below. This is the real-time heartbeat.

**Layout:** Scrollable list of the latest 20 firewall decisions, newest first.

Each entry is a single row:
```
[Verdict Badge]  [Agent Name]  →  [Merchant]  ₹[Amount]  [Time ago]  [→ Detail]
```

Example rows:
```
✅ ALLOWED   ShopBot    →  decathlon@axisbank    ₹2,400    2 min ago    →
❌ BLOCKED   ShopBot    →  giftcard-store@upi    ₹5,000    8 min ago    →
⚠️ STEP-UP   TravelBot  →  makemytrip@icici      ₹12,000   15 min ago   →
```

**Verdict badges:** Colored pills (green/red/amber) with white text. Small, not big blocks.

**Real-time behavior:**
- New entries slide in from the top with a subtle animation (fade + slide down, 300ms)
- Use **WebSocket or SSE** — NOT polling. Polling every 5s looks jittery and fake. WebSocket makes it feel alive.

**Clicking any row** → opens the Transaction Detail Drawer (Tab 3 feature, but accessible from here too)

> [!TIP]
> **Add a tiny sound option (muted by default) for blocked transactions.** A subtle "ding" when a block happens makes live demos feel electric. Keep it off by default — add a small 🔔 toggle icon in the feed header.

**Empty state:** Centered message: "No activity yet. Once your agent starts transacting, decisions will appear here in real-time." + subtle illustration of a shield.
**Loading state:** 5 skeleton rows with shimmer animation.

---

### 1.5 Pending Step-Up Cards

If any transactions are waiting for human approval, show them as action cards between the stat bar and the feed.

**Each card:**
```
┌─────────────────────────────────────────────┐
│ ⚠️ Step-Up Required              ⏱️ 1:42    │
│                                             │
│ TravelBot wants to pay ₹12,000             │
│ to makemytrip@icici                        │
│ Intent: "book flight to Delhi"             │
│ Trigger: Amount exceeds auto-approve (₹100)│
│                                             │
│          [ ✅ Approve ]  [ ❌ Deny ]         │
└─────────────────────────────────────────────┘
```

- **Countdown timer** (top-right) — counts down from 2:00. Visual urgency.
- When timer hits 0 → auto-denied, card fades out with a red flash
- Approve/Deny buttons are large and far apart (prevent accidental clicks)
- Card has amber left border

**Layout:** Horizontal scroll if multiple step-ups. Max 3 visible, "+N more" indicator.

> [!TIP]
> **The countdown timer should change color as it depletes:** Blue (>1:00) → Amber (0:30–1:00) → Red (<0:30). This creates visual urgency without a blaring alarm.

---

### 1.6 Kill Switch (Sidebar — always visible)

Already described in the navigation section. Appears at the bottom of the sidebar on every page.

**Design:**
- Large red button: "⏹ PAUSE ALL AGENTS"
- On hover: darkens to deeper red, slight scale-up
- On click: confirmation modal ("Are you sure? This will immediately pause all agents and mandates.")
- After activation: button turns to "▶️ RESUME ALL" in green
- State persists across sessions

> [!TIP]
> **Don't use a toggle switch for the kill switch — use a button.** Toggles can be accidentally flipped. A button with a confirmation modal is deliberate. In the demo, the presenter can dramatically hit the kill switch while narrating an attack scenario — it's a moment.

---

## Tab 2: 📜 Mandates

**Purpose:** Create, view, manage all the permission slips given to agents.
**Max items on screen:** 5

---

### 2.1 Create Mandate Wizard

Accessed via a prominent "+ Create Mandate" button (blue, top-right of the Mandates page).

Opens as a **full-page multi-step form** with a progress stepper at the top.

#### Step 1 of 5 — Intent
```
┌──────────────────────────────────────────┐
│  Step 1 of 5: What should the agent do?  │
│  ────────────────────────────────────     │
│                                          │
│  Intent:                                 │
│  ┌──────────────────────────────────┐    │
│  │ buy running shoes under ₹3000   │    │
│  └──────────────────────────────────┘    │
│                                          │
│  💡 Be specific. This is what the        │
│     firewall checks purchases against.   │
│                                          │
│  Examples:                               │
│  • "buy running shoes"                   │
│  • "pay monthly electricity bill"        │
│  • "book a cab to the airport"           │
│                                          │
│              [Next →]                    │
└──────────────────────────────────────────┘
```

**Design notes:**
- Large text input, auto-focused on page load
- Helpful examples below in muted text
- "💡" hint explaining why this matters (plain language)

> [!TIP]
> **Pre-populate intent from templates if the user selected one.** The wizard should feel fast, not like a government form. If the user clicked "Monthly Groceries" template, this field already says "buy groceries" and they just hit Next.

#### Step 2 of 5 — Merchant Scope

Two options presented as selectable cards:

**Option A: Category** — dropdown multi-select (e.g., Footwear, Groceries, Travel)
**Option B: Specific Merchants** — text input for VPA addresses (e.g., `decathlon@axisbank`)

- Can use both (category + specific merchants = allowlist union)
- Each added merchant/category appears as a removable chip/tag
- Search-as-you-type for known merchants

> [!TIP]
> **Show a warning if the user leaves scope wide open:** "⚠️ No merchant restriction means the agent can pay anyone within budget." This isn't an error — it's a nudge toward security. Judges notice product thinking like this.

#### Step 3 of 5 — Spend Caps

Three inputs in a clean form:

| Field | Input Type | Default | Help Text |
|-------|-----------|---------|-----------|
| Per-transaction limit | Number (₹) | ₹3,000 | "Max the agent can spend in one payment" |
| Cumulative total | Number (₹) | ₹3,000 | "Total budget across all payments" |
| Max transaction count | Number | 1 | "How many payments can the agent make" |

- **Visual budget preview:** A small bar showing "₹3,000 total / max 1 transaction / up to ₹3,000 each"
- Validation: per-txn ≤ cumulative (show inline error if violated)

> [!TIP]
> **Show a "smart suggestion" based on intent.** If intent says "buy running shoes" → suggest per-txn ₹5,000, cumulative ₹5,000, count 1. If "monthly groceries" → suggest per-txn ₹2,000, cumulative ₹8,000, count 10. This makes the wizard feel intelligent. Even hardcoded suggestions per keyword are impressive.

#### Step 4 of 5 — Time Window

Two date-time pickers:

| Field | Input | Default |
|-------|-------|---------|
| Valid from | Date + time picker | Now |
| Valid until | Date + time picker | +24 hours |

- **Quick presets:** "Next 1 hour", "Today only", "This week", "This month" — clickable chips above the pickers
- Visual timeline bar showing the active window

> [!TIP]
> **Default to the narrowest reasonable window.** "Today only" is a better default than "1 month." Narrow defaults train users toward security. Judges notice this design choice.

#### Step 5 of 5 — Review & Sign

Full summary of all choices in a clean, readable card:

```
┌──────────────────────────────────────────────────┐
│  📋 Mandate Summary                              │
│  ────────────────────────────────────             │
│                                                  │
│  Intent:      "Buy running shoes"                │
│  Agent:       ShopBot v1                         │
│  Merchants:   decathlon@axisbank (Footwear)      │
│  Budget:      ₹3,000 total (₹3,000 per txn)     │
│  Payments:    Max 1 transaction                  │
│  Window:      Today 10:00 AM – 10:00 PM          │
│                                                  │
│  🔐 Signing with your device key                │
│                                                  │
│      [ ← Back ]    [ ✅ Sign & Activate ]        │
└──────────────────────────────────────────────────┘
```

- "Sign & Activate" button in green, prominent
- On click: brief signing animation (lock icon spins → checkmark), then redirect to mandate detail page
- Show the mandate ID after creation (e.g., `mnd_8f2c91`)

> [!TIP]
> **Add a "Save as Template" checkbox on the review step.** One small checkbox: "💾 Save as template for reuse." Zero friction, huge utility. Judges see it and think "they thought about recurring use cases."

---

### 2.2 Active Mandates Table

The main view on the Mandates tab. A clean data table.

**Columns:**

| Column | Content | Width |
|--------|---------|-------|
| Health | 🟢 🟡 🔴 dot | 40px |
| Intent | Truncated text (e.g., "Buy running shoes") | flex |
| Agent | Agent name | 120px |
| Budget | Progress bar (e.g., ₹2,400/₹3,000) | 180px |
| Expires | Relative time (e.g., "in 3h") | 100px |
| Status | Badge (Active / Expired / Revoked) | 100px |
| Actions | ⋮ menu (view, revoke, extend) | 60px |

**Filters (above table):**
- Agent dropdown
- Status dropdown (Active / Expired / Revoked / All)
- Search by intent text

**Sorting:** Click column headers to sort. Arrow indicator shows sort direction.

**Row hover:** Background shifts to light gray, entire row is clickable → opens Mandate Detail.

> [!TIP]
> **The Health dot (column 1) is the Mandate Health Score (#4).** It's subtle but always visible. Users learn to scan the leftmost column for red/yellow dots. No need for a separate "Health" page — it's integrated into the table they already use every day.

**Empty state:** "No mandates yet. Create your first mandate to get started." + prominent "+ Create Mandate" button centered.

---

### 2.3 Mandate Detail Page

Opens when clicking a row in the mandates table. Full page (not a drawer — mandates deserve full real estate).

**Layout:**

```
┌──────────────────────────────────────────────────────┐
│  ← Back to Mandates                                 │
│                                                      │
│  📜 "Buy running shoes"          🟡 WATCH    [Revoke]│
│  mnd_8f2c91 · ShopBot v1 · Active                   │
│  ─────────────────────────────────────────            │
│                                                      │
│  ┌─── Constraints ───┐  ┌─── Budget Usage ─────────┐│
│  │ Merchants:        │  │                           ││
│  │  decathlon@axis.. │  │  ████████████░░░░  80%    ││
│  │ Per-txn: ₹3,000   │  │  ₹2,400 of ₹3,000        ││
│  │ Total:   ₹3,000   │  │                           ││
│  │ Count:   1 of 1   │  │  Transactions: 0 of 1     ││
│  │ Window:  Today    │  │  Time left: 3h 14m        ││
│  │  10AM – 10PM      │  │                           ││
│  └───────────────────┘  └───────────────────────────┘│
│                                                      │
│  ┌─── Transaction History ──────────────────────────┐│
│  │ (transactions made under this mandate)           ││
│  │ ✅ decathlon@axisbank  ₹2,400  2 hours ago       ││
│  └──────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────┘
```

**Key elements:**
- **Health badge** (top-right, color-coded): 🟢 Healthy / 🟡 Watch / 🔴 Critical
- **Budget progress bar:** Visual, color changes with usage (green <50%, amber 50-80%, red >80%)
- **Constraints card:** Plain-language breakdown (NOT raw JSON)
- **Revoke button:** Red outline button, top-right. Confirmation modal on click.
- **Transaction history:** Subset of transactions filtered to this mandate

> [!TIP]
> **Show a "What happens when this mandate is revoked?" tooltip on the Revoke button.** Text: "The agent will no longer be able to use this mandate. Any pending transactions will be blocked." This prevents anxiety and demonstrates thoughtful UX.

---

### 2.4 Mandate Health Score ⭐ (Custom Suggestion #4)

Not a separate page — integrated into every mandate display (table, detail page, dashboard).

**Scoring logic (displayed as tooltip on hover over the health dot):**

| Signal | Weight | Green | Yellow | Red |
|--------|--------|-------|--------|-----|
| Budget used | 40% | <50% | 50–85% | >85% |
| Time remaining | 25% | >50% left | 10–50% left | <10% left |
| Transaction count | 20% | <50% used | 50–85% used | >85% used |
| Blocked attempts | 15% | 0 blocks | 1–2 blocks | 3+ blocks |

**Hover tooltip example:**
```
🟡 WATCH — Score: 62/100

Budget: 80% used (₹2,400/₹3,000)     ⚠️
Time: 3h remaining (25% left)         ⚠️
Transactions: 0 of 1 used             ✅
Blocked attempts: 0                    ✅
```

> [!TIP]
> **The health score is computed client-side from existing data — no backend work needed.** Budget usage, time remaining, count usage, and block count are all already in the mandate object. A simple weighted formula gives a 0–100 score, mapped to green/yellow/red. This means you can implement this feature in 30 minutes and it looks like a premium feature.

---

### 2.5 Mandate Templates

A secondary section on the Mandates page, below the active mandates table (or as a sub-tab: "Active" | "Templates").

**Pre-built templates:**

| Template | Intent | Scope | Budget | Window |
|----------|--------|-------|--------|--------|
| 🛒 Monthly Groceries | "buy groceries" | category:groceries | ₹8,000/month, ₹2,000/txn, 10 txns | 30 days |
| 👟 One-time Purchase | "buy [item]" | user-specified | ₹5,000, 1 txn | 24 hours |
| 🔄 Recurring Subscription | "pay [service] subscription" | user-specified merchant | ₹1,000/month, 1 txn | 30 days, auto-renew |
| ✈️ Travel Booking | "book travel to [destination]" | category:travel | ₹25,000, 3 txns | 7 days |

Each template = a card. Clicking → opens the Create Mandate Wizard with fields pre-filled.

> [!TIP]
> **Show "Used 12 times" on templates that the user has previously used.** Social proof (with yourself) makes templates feel valuable, not decorative.

---

## Tab 3: 🔍 Transactions

**Purpose:** Full transaction history + the signature demo screen (Transaction Detail Drawer).
**Max items on screen:** 5

---

### 3.1 Transaction Log

Full-page data table of every firewall decision, ever.

**Columns:**

| Column | Content | Width |
|--------|---------|-------|
| Verdict | Color-coded badge (Allowed/Blocked/Step-up) | 100px |
| Agent | Agent name | 120px |
| Merchant | VPA address | flex |
| Amount | ₹X,XXX | 100px |
| Intent Match | Confidence % (colored) | 100px |
| Date/Time | Timestamp | 160px |
| Actions | "View Details" link | 100px |

**Filter bar (above table):**
- Verdict filter: All / Allowed / Blocked / Step-up (as clickable tabs/pills)
- Agent dropdown
- Merchant search
- Amount range (min–max)
- Date range picker

**Pagination:** 25 rows per page, page navigation at bottom.

> [!TIP]
> **Make the verdict filter tabs look like the colored badges.** "Allowed" tab in green, "Blocked" in red, "Step-up" in amber. Active filter fills the color, inactive filters are outlined. This way the entire page feels color-coded based on what you're looking at — reviewing blocks? The page tints red. Reviewing approvals? Green. Visually immersive.

---

### 3.2 Transaction Detail Drawer ⭐⭐⭐ (THE Demo Screen)

Opens as a **slide-in panel from the right** (60% of screen width) when clicking any transaction row. The table dims slightly behind it.

**Layout inside the drawer:**

```
┌─────────────────────────────────────────────────┐
│  ✕ Close                                        │
│                                                 │
│  ❌ BLOCKED                         ₹5,000      │
│  ShopBot → giftcard-store@upi                   │
│  Sep 3, 2026 · 2:14 PM                         │
│  ────────────────────────────────────            │
│                                                 │
│  🔗 CHECK CHAIN                                 │
│  ┌─────────────────────────────────────────┐    │
│  │ ✅ Signature Check                      │    │
│  │    Mandate signature verified (Ed25519) │    │
│  ├─────────────────────────────────────────┤    │
│  │ ✅ Freshness Check                      │    │
│  │    Within validity window (2h remaining)│    │
│  ├─────────────────────────────────────────┤    │
│  │ ✅ Replay Check                         │    │
│  │    Nonce not previously consumed        │    │
│  ├─────────────────────────────────────────┤    │
│  │ ✅ Ceiling Check                        │    │
│  │    ₹5,000 ≤ ₹3,000 per-txn cap  ← WAIT│    │
│  │    (Passed: ₹0/₹3,000 cumulative)      │    │
│  ├─────────────────────────────────────────┤    │
│  │ ❌ Scope Check                          │    │
│  │    "giftcard-store@upi" NOT in list:    │    │
│  │    [decathlon@axisbank]                 │    │
│  ├─────────────────────────────────────────┤    │
│  │ ⬜ Intent Check — SKIPPED               │    │
│  │    (Blocked before reaching this check) │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  💬 VERDICT REASON                              │
│  "Merchant 'giftcard-store@upi' is not in the  │
│   approved merchant list for this mandate.      │
│   Allowed: decathlon@axisbank."                 │
│                                                 │
│  📊 INTENT MATCH ──────── 12% ──── ❌ Low       │
│  ████░░░░░░░░░░░░░░░░░░░░░░░░░░                │
│  Mandate: "buy running shoes"                   │
│  Payment: "Gift card purchase"                  │
│                                                 │
│  [ 🔄 Replay Attack ]  [ 📜 View Raw JSON ]    │
│  [ 📜 Create Mandate for This ]                 │
│                                                 │
└─────────────────────────────────────────────────┘
```

**Check Chain Visualization — Design Details:**

Each check is a horizontal bar in a vertical stack. Connected by a thin vertical line (pipeline visual).

- **Passed checks:** Green left border, ✅ icon, green check name, gray explanation text
- **Failed checks:** Red left border, ❌ icon, red check name, bold explanation text
- **Skipped checks:** Gray left border, ⬜ icon, gray italic text "Skipped (blocked before reaching this check)"
- **The failing check should be visually prominent** — slightly larger, maybe a light red background (#FEF2F2)

> [!IMPORTANT]
> **This drawer is THE most important UI element in the entire product.** Polish it more than anything else. Every pixel matters here. When a judge opens this drawer, they should immediately understand: (1) what checks ran, (2) which one caught the problem, (3) why, in English. If this drawer is confusing, the entire demo fails. If it's beautiful and clear, you win.

> [!TIP]
> **Add a subtle connecting line between checks** (like a pipeline/flowchart). A 2px gray vertical line on the left, with each check as a node. This reinforces "chain" — it's not just a list, it's a sequential pipeline. Green line up to the pass point, red line at the failure point, gray dashed line after.

---

### 3.3 Live Attack Replay ⭐ (Custom Suggestion #1)

A "🔄 Replay Attack" button at the bottom of the Transaction Detail Drawer.

**What happens on click:**
1. The check chain resets — all items go gray/empty
2. A 200ms delay
3. Check 1 (Signature) lights up green ✅ — with a small "ding" sound and a brief green flash
4. 400ms delay
5. Check 2 (Freshness) lights up green ✅
6. 400ms delay
7. Check 3 (Replay) lights up green ✅
8. 400ms delay
9. Check 4 (Ceiling) lights up green ✅
10. 400ms delay
11. Check 5 (Scope) lights up **RED ❌** — with a "thud" sound, the bar flashes red, and a subtle screen shake (1-2px, 100ms)
12. Check 6 stays gray — "Skipped"
13. The verdict banner at the top animates from neutral to **"❌ BLOCKED"** with a slide-in

**Total duration:** ~3 seconds. Fast enough to not bore anyone, slow enough to follow each step.

**Implementation:**
```css
@keyframes checkPass {
    0% { opacity: 0; transform: translateX(-10px); }
    100% { opacity: 1; transform: translateX(0); }
}

@keyframes checkFail {
    0% { opacity: 0; transform: translateX(-10px); }
    50% { transform: translateX(3px); } /* shake */
    75% { transform: translateX(-3px); }
    100% { opacity: 1; transform: translateX(0); background: #FEF2F2; }
}
```

> [!TIP]
> **This is the single best demo moment in the product.** During the live pitch, the presenter says "Watch this" — clicks Replay — and the audience sees the attack get caught in slow motion. Practice the timing. Make sure the animation is smooth at 60fps. Test on the actual demo laptop.

---

### 3.4 Intent Match Confidence Meter ⭐ (Custom Suggestion #5)

Shown inside the Transaction Detail Drawer, below the check chain.

**Design:** A horizontal progress bar + percentage number + rating label.

```
📊 Intent Match: 12% ─── ❌ Low Confidence

████░░░░░░░░░░░░░░░░░░░░░░░░  12%

Mandate intent: "buy running shoes"
Payment context: "Gift card — ₹5,000"
```

**Color mapping:**
| Range | Color | Label |
|-------|-------|-------|
| 80–100% | Green | ✅ High Match |
| 50–79% | Amber | ⚠️ Partial Match |
| 0–49% | Red | ❌ Low Confidence |

**The bar fills to the percentage, colored accordingly.**

Below the bar: two lines showing the mandate intent vs. what the agent actually tried to buy. The mismatch is immediately visible.

> [!TIP]
> **Even if the intent check wasn't the check that blocked the transaction, still show the confidence meter.** A transaction might be blocked by the scope check, but seeing "Intent Match: 12%" reinforces that MULTIPLE checks would have caught it. This is the "defense-in-depth" argument made visual.

---

### 3.5 Quick Mandate from Block ⭐ (Custom Suggestion #6)

A button inside the Transaction Detail Drawer, shown **only on blocked transactions.**

**Button text:** "📜 Create Mandate for This"

**What it does:**
1. Opens the Create Mandate Wizard
2. Pre-fills:
   - Intent: inferred from the blocked payment context
   - Merchant: the merchant VPA from the blocked transaction
   - Amount: the amount from the blocked transaction (as per-txn cap)
   - Time window: defaults to "next 24 hours"
3. User just reviews and signs — 2 clicks to unblock

**When to show this button:**
- Only on BLOCKED transactions (not allowed or step-up)
- Highlighted with a blue outline (not red — this is a recovery action, not an alert)

> [!TIP]
> **Position this as "fixing false positives in 2 clicks."** In the pitch, mention: "When the firewall is too strict, the user doesn't have to start from scratch — they fix it from the blocked transaction itself." This shows the product isn't just a blocker — it's a complete workflow.

---

### 3.6 Step-Up Approval Queue

A sub-section at the top of the Transactions tab (or a sub-tab: "All Transactions" | "Pending Approvals").

Same design as dashboard step-up cards (section 1.5), but here they're in a full-width list with more detail.

**Each item shows:**
- Agent name, merchant, amount
- Which check triggered the step-up (e.g., "Amount exceeds auto-approve threshold")
- Intent match score
- Countdown timer
- Approve / Deny buttons
- "View full check chain" link → opens detail drawer

> [!TIP]
> **Show a count badge on the "Transactions" tab in the sidebar when there are pending step-ups.** A small red circle with a number (like unread messages). This pulls attention even when the user is on another tab.

---

## Tab 4: 📈 Analytics

**Purpose:** Quantified proof that the firewall works. Numbers for judges.
**Max items on screen:** 4

---

### 4.1 Ablation Matrix ⭐ (The Single Most Convincing Artifact)

A table/heatmap showing which security check catches which attack.

**Layout:** Interactive table.

| Attack ↓ \ Check → | Signature | Freshness | Replay | Ceiling | Scope | Intent |
|---------------------|-----------|-----------|--------|---------|-------|--------|
| 1. Prompt Injection Overspend | ➖ | ➖ | ➖ | ➖ | ✅ Caught | ✅ Caught |
| 2. Replay Attack | ➖ | ➖ | ✅ Caught | ➖ | ➖ | ➖ |
| 3. Slicing Drain | ➖ | ➖ | ➖ | ✅ Caught | ➖ | ➖ |
| 4. Merchant Substitution | ➖ | ➖ | ➖ | ➖ | ✅ Caught | ➖ |
| 5. Intent Drift | ➖ | ➖ | ➖ | ➖ | ➖ | ✅ Caught |
| 6. Metadata Leakage | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ (Scrubber) |

**Cell colors:**
- ✅ Caught → Green cell background
- ➖ Not relevant → Light gray cell
- ❌ Missed → Red cell (none in our case — the point is full coverage)

**Hover on any cell** → tooltip with explanation: "The scope check catches prompt injection because injected instructions usually redirect payment to a merchant outside the user's allowlist."

**Layout:** Full-width card, center of the analytics page. This should be visually prominent.

> [!TIP]
> **Use color intensity for the heatmap effect.** Cells with "Caught" should be a vivid green, not just text. The visual pattern of green cells forming a diagonal across the matrix tells the story instantly: "every attack is caught, by a different check." This is the image that stays in a judge's mind.

---

### 4.2 Key Metrics Cards

Three metric cards in a row (same style as dashboard stat bar).

| Metric | Example Value | Color | Judge Impact |
|--------|--------------|-------|--------------|
| **Block Rate** | 100% (6/6 attacks blocked) | Green | "It works" |
| **False Positive Rate** | 2% (1/50 benign transactions wrongly blocked) | Green (low is good) | "It doesn't over-block" |
| **Latency p50 / p95** | 23ms / 87ms | Blue | "It's fast enough for real-time" |

> [!TIP]
> **Show latency as a spark line (tiny inline chart) next to the number.** Even a simple 20-point line chart showing recent latency readings makes the metric feel live and monitored, not static.

---

### 4.3 Spend Breakdown Charts

Two charts side by side:

**Left chart:** Donut/pie chart — spend by category (Footwear 40%, Groceries 35%, Travel 25%)
**Right chart:** Bar chart — spend by agent (ShopBot ₹8,400, TravelBot ₹12,000)

- Use the indigo/blue/green palette — no clashing colors
- Tooltips on hover showing exact amounts
- Legend below each chart

> [!TIP]
> **Use Chart.js or a simple SVG-based chart — don't import a heavy library like D3.js for the hackathon.** Chart.js is 60KB, has great defaults, and generates beautiful charts with 10 lines of code. Keep it light.

---

### 4.4 Activity Over Time

A line chart showing transactions per day (last 30 days), with two lines:
- **Green line:** Allowed transactions
- **Red line:** Blocked transactions

Y-axis: count. X-axis: date.

Hover shows exact count per day.

> [!TIP]
> **If blocked transactions spike on a particular day, that's a story the presenter can narrate:** "On day 14, we simulated a coordinated attack — you can see the block count spike. The firewall caught everything." Charts-as-narrative is what separates a demo from a product.

---

## Tab 5: 🤖 Agents

**Purpose:** Manage which AI agents are connected and what they're doing.
**Max items on screen:** 4

---

### 5.1 Connected Agents List

A card-based grid (not a table — agents deserve visual identity).

**Each agent card:**
```
┌────────────────────────────┐
│  🤖 ShopBot v1             │
│  agent_shopper_v1          │
│  ──────────────────────    │
│  Status: 🟢 Active         │
│  Mandates: 3 active        │
│  Last active: 2 min ago    │
│                            │
│  [ View Details ]          │
└────────────────────────────┘
```

- Status dot: Green (active), Gray (paused), Red (revoked)
- Cards in a 3-column grid
- "+ Add Agent" card at the end (dashed border, blue plus icon)

> [!TIP]
> **Give each agent a distinct auto-generated icon or avatar** (like GitHub's identicons). Even a simple colored circle with initials (SB, TB) makes agents feel like entities, not just IDs. Judges respond to personality in interfaces.

---

### 5.2 Agent Detail Page

Opens on clicking an agent card. Full page.

**Sections:**
1. **Agent info header:** Name, ID, status, date connected
2. **Mandates held:** Mini table of mandates granted to this agent (with health dots)
3. **Transaction history:** Filtered to this agent only
4. **Action buttons:** Pause Agent (amber), Revoke Agent (red)

---

### 5.3 Add Agent Flow

Simple modal or slide-in panel:
1. Agent name (text input)
2. Agent ID (text input or auto-generated)
3. API key display (generated, copy button)
4. "Connect" button

> [!TIP]
> **Show a "Test Connection" button that pings a mock endpoint** and shows a green checkmark. Even if it's simulated, it feels like a real integration flow. Takes 10 minutes to implement, looks professional.

---

### 5.4 Pause / Revoke Controls

- **Pause:** Agent can't transact, but mandates are preserved. Resumable.
- **Revoke:** Agent is disconnected, all its mandates are revoked. Permanent (must re-add).
- Both require confirmation modals with clear consequences explained.

---

## Tab 6: ⚙️ Settings

**Purpose:** Profile, preferences, security, audit exports.
**Max items on screen:** 5

---

### 6.1 Profile & Linked Accounts

- User name, email
- Linked UPI VPA (e.g., `user_vpa@bank`) — with verified badge
- Account created date

---

### 6.2 Notification Preferences

Toggle switches for:
| Notification Type | Default |
|-------------------|---------|
| Blocked transactions | ✅ On |
| Step-up approvals | ✅ On |
| Mandate expiration warnings | ✅ On |
| Weekly summary | ✅ On |
| Every allowed transaction | ❌ Off |

---

### 6.3 Security Settings

- **Signing key info:** Key fingerprint display (truncated hash), creation date
- **Rotate key** button (with warning modal)
- **Active sessions:** List with "Sign out all" button
- **Auto-approve threshold:** Slider from ₹0 to ₹500 — transactions below this amount skip step-up and auto-allow (if all other checks pass)

> [!TIP]
> **The auto-approve threshold slider is a great demo talking point:** "For micro-transactions like ₹10 data top-ups, you don't want to approve manually every time. Set the threshold, and only meaningful amounts require your attention." This shows the product respects the user's time.

---

### 6.4 Audit Trail Export

- "📥 Export Decision Log" button
- Date range picker
- Format: CSV or PDF
- **Tamper-evident hash chain** shown at the bottom of the export — each entry's SHA-256 hash includes the previous entry's hash
- Visual indicator: "🔒 This log is tamper-evident. Verify integrity by re-computing the hash chain."

---

### 6.5 About / Help

- Product version
- Link to documentation (can be a mock)
- "Report a bug" link
- Credits / team

---

## 🏁 Mocked Screens (Tier 3 — Just Enough to Feel Complete)

### Enterprise Admin Console (1 screen)
A static page showing:
- "Organization: Acme Corp"
- Org-wide stats: 24 users, 156 active mandates, 99.2% block rate
- User list table with role badges (Admin / Auditor / Viewer)
- This screen is **non-interactive** — just a visual. It exists to show "this product scales to organizations."

### Developer Portal (1 screen)
A static page showing:
- API key display (masked)
- Sandbox / Live toggle
- Webhook URL input field
- Mini code snippet: `curl -X POST .../verify-mandate`
- This screen says "there's an integration path" without building one.

---

## 📐 Responsive Behavior (Laptop Only)

| Viewport | Behavior |
|----------|----------|
| ≥1400px | Full layout, sidebar + wide content |
| 1024–1399px | Sidebar collapses to icons only, content fills |
| <1024px | Not supported — show a message: "Please use a laptop or desktop for the best experience." |

---

## ⚡ Performance & Polish Checklist

- [ ] All transitions at `0.2s ease` — nothing instant, nothing laggy
- [ ] Skeleton loading states on every data-dependent section
- [ ] Empty states with helpful messages on every list/table
- [ ] Error states with retry buttons (not just red text)
- [ ] WebSocket/SSE for live feed — never polling
- [ ] Hover effects on every clickable element (cursor + visual feedback)
- [ ] Focus outlines for keyboard navigation (accessibility)
- [ ] Consistent 8px spacing grid throughout
- [ ] Inter font loaded and applied globally
- [ ] Color palette used consistently — no ad-hoc colors anywhere
- [ ] All numbers formatted with Indian number system (₹1,00,000 not ₹100,000)
- [ ] Timestamps in relative format ("2 min ago") with full timestamp on hover tooltip

> [!IMPORTANT]
> **Test the entire UI on the actual demo laptop before the pitch.** Font rendering, animation smoothness, and color accuracy vary between screens. A gradient that looks beautiful on your dev machine might wash out on a projector. Bring your own HDMI adapter. Have a backup plan (screenshots) if the projector fails.
