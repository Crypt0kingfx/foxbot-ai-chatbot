-- Casino FoxCoin <-> PROMO conversions that look like double-spends.
-- READ-ONLY: everything runs inside a READ ONLY transaction that is rolled
-- back at the end; Postgres rejects any write inside it.
--
-- Run it yourself, e.g.:
--   psql "$DATABASE_URL" -f docs/casino-double-spend-check.sql
--
-- What it can see:
--   * casino_conversion_attempts: one row per conversion (deposit = FoxCoin
--     -> PROMO, cashout = PROMO -> FoxCoin), with created/updated times.
--   * foxbot_state (state_key 'foxbot_data'): the FoxCoin economy as of the
--     LAST SAVE. Each casino FoxCoin debit/credit is recorded there under
--     its idempotency key, with the balance it left behind:
--       foxcoin_economy.by_creator.<creator>.processed_deductions.<key>
--       foxcoin_economy.by_creator.<creator>.processed_credits.<key>
--
-- The race this looks for: two conversions by the same viewer whose
-- FoxCoin legs ran at the same time and both read the SAME starting
-- balance. A deposit records balance_after = start - cost, so two debits
-- that read the same start satisfy  a.balance_after + a.cost
-- = b.balance_after + b.cost.  In a correct, serialized history the second
-- debit starts from the first one's balance_after instead. A legitimate
-- coincidence needs another change of exactly the right size in between,
-- so treat hits as "look at this", not proof.
--
-- Limits: the FoxCoin side only shows what was in the last saved
-- snapshot. A double credit of the SAME key leaves one entry, so it can't
-- be seen here (only in the 50-row transaction history, which rolls over).

BEGIN TRANSACTION READ ONLY;

-- 1. Same-viewer conversions in the same direction whose attempts overlapped
--    in time (created_at .. updated_at). The race needs overlap, so this is
--    the candidate list; queries 2 and 3 narrow it down.
SELECT a.creator_id,
       a.user_id,
       a.direction,
       a.idempotency_key  AS key_a,
       b.idempotency_key  AS key_b,
       a.created_at       AS a_started,
       a.updated_at       AS a_finished,
       b.created_at       AS b_started,
       b.updated_at       AS b_finished,
       a.foxcoin_amount   AS a_foxcoins,
       b.foxcoin_amount   AS b_foxcoins,
       a.status           AS a_status,
       b.status           AS b_status
FROM casino_conversion_attempts a
JOIN casino_conversion_attempts b
  ON  b.creator_id = a.creator_id
  AND b.user_id    = a.user_id
  AND b.direction  = a.direction
  AND b.idempotency_key > a.idempotency_key
  AND a.created_at <= b.updated_at
  AND b.created_at <= a.updated_at
ORDER BY a.created_at DESC;

-- 2. LIKELY DOUBLE-SPENDS: overlapping deposits whose FoxCoin debits both
--    started from the same balance (both passed the funds check on one
--    balance). Each row is a pair; `coins_overspent` is how many FoxCoins
--    were spent that the viewer may not have had.
WITH debits AS (
    SELECT econ.creator_id,
           d.key                                AS idempotency_key,
           d.value ->> 'viewer'                 AS viewer,
           -((d.value ->> 'amount')::bigint)    AS cost,
           (d.value ->> 'balance')::bigint      AS balance_after
    FROM foxbot_state st
    CROSS JOIN LATERAL jsonb_each(COALESCE(st.payload -> 'foxcoin_economy' -> 'by_creator', '{}'::jsonb))
         AS econ(creator_id, economy)
    CROSS JOIN LATERAL jsonb_each(COALESCE(econ.economy -> 'processed_deductions', '{}'::jsonb)) AS d
    WHERE st.state_key = 'foxbot_data'
),
deposits AS (
    SELECT ca.*, dbt.cost, dbt.balance_after
    FROM casino_conversion_attempts ca
    JOIN debits dbt
      ON dbt.creator_id = ca.creator_id
     AND dbt.idempotency_key = ca.idempotency_key
    WHERE ca.direction = 'deposit'
)
SELECT a.creator_id,
       a.user_id,
       a.idempotency_key                  AS key_a,
       b.idempotency_key                  AS key_b,
       a.created_at                       AS a_started,
       b.created_at                       AS b_started,
       a.balance_after + a.cost           AS shared_starting_balance,
       a.cost                             AS a_cost,
       b.cost                             AS b_cost,
       a.balance_after                    AS a_balance_after,
       b.balance_after                    AS b_balance_after,
       GREATEST(0, (a.cost + b.cost) - (a.balance_after + a.cost)) AS coins_overspent,
       a.status                           AS a_status,
       b.status                           AS b_status
FROM deposits a
JOIN deposits b
  ON  b.creator_id = a.creator_id
  AND b.user_id    = a.user_id
  AND b.idempotency_key > a.idempotency_key
  AND a.balance_after + a.cost = b.balance_after + b.cost
  AND a.created_at <= b.updated_at + INTERVAL '5 seconds'
  AND b.created_at <= a.updated_at + INTERVAL '5 seconds'
ORDER BY a.created_at DESC;

-- 3. LOST CASHOUT CREDITS (the opposite harm: the viewer was short-changed).
--    Overlapping cashouts whose FoxCoin credits both started from the same
--    balance, so one credit overwrote the other.
WITH credits AS (
    SELECT econ.creator_id,
           c.key                              AS idempotency_key,
           (c.value ->> 'amount')::bigint     AS amount,
           (c.value ->> 'balance')::bigint    AS balance_after
    FROM foxbot_state st
    CROSS JOIN LATERAL jsonb_each(COALESCE(st.payload -> 'foxcoin_economy' -> 'by_creator', '{}'::jsonb))
         AS econ(creator_id, economy)
    CROSS JOIN LATERAL jsonb_each(COALESCE(econ.economy -> 'processed_credits', '{}'::jsonb)) AS c
    WHERE st.state_key = 'foxbot_data'
),
cashouts AS (
    SELECT ca.*, cr.amount, cr.balance_after
    FROM casino_conversion_attempts ca
    JOIN credits cr
      ON cr.creator_id = ca.creator_id
     AND cr.idempotency_key = ca.idempotency_key
    WHERE ca.direction = 'cashout'
)
SELECT a.creator_id,
       a.user_id,
       a.idempotency_key              AS key_a,
       b.idempotency_key              AS key_b,
       a.created_at                   AS a_started,
       b.created_at                   AS b_started,
       a.balance_after - a.amount     AS shared_starting_balance,
       a.amount                       AS a_credit,
       b.amount                       AS b_credit
FROM cashouts a
JOIN cashouts b
  ON  b.creator_id = a.creator_id
  AND b.user_id    = a.user_id
  AND b.idempotency_key > a.idempotency_key
  AND a.balance_after - a.amount = b.balance_after - b.amount
  AND a.created_at <= b.updated_at + INTERVAL '5 seconds'
  AND b.created_at <= a.updated_at + INTERVAL '5 seconds'
ORDER BY a.created_at DESC;

-- 4. PROMO MINTED WITH NO RECORDED FOXCOIN DEBIT: deposits that reached
--    'debited'/'completed' (so PROMO was or will be credited) but whose
--    FoxCoin debit is missing from the saved economy. Causes: the debit was
--    lost before a save (crash, or an older snapshot overwriting a newer
--    one), or the snapshot simply hasn't been saved since -- so ignore rows
--    from the last minute or two.
SELECT ca.creator_id,
       ca.user_id,
       ca.idempotency_key,
       ca.status,
       ca.foxcoin_amount,
       ca.promo_amount,
       ca.created_at,
       ca.updated_at
FROM casino_conversion_attempts ca
LEFT JOIN foxbot_state st ON st.state_key = 'foxbot_data'
WHERE ca.direction = 'deposit'
  AND ca.status IN ('debited', 'completed')
  AND (st.payload -> 'foxcoin_economy' -> 'by_creator' -> ca.creator_id
                  -> 'processed_deductions' -> ca.idempotency_key) IS NULL
ORDER BY ca.created_at DESC;

-- 5. Same check for cashouts: PROMO taken back but no FoxCoin credit in the
--    saved economy (status 'completed' means the credit was supposed to land).
SELECT ca.creator_id,
       ca.user_id,
       ca.idempotency_key,
       ca.status,
       ca.foxcoin_amount,
       ca.promo_amount,
       ca.created_at,
       ca.updated_at
FROM casino_conversion_attempts ca
LEFT JOIN foxbot_state st ON st.state_key = 'foxbot_data'
WHERE ca.direction = 'cashout'
  AND ca.status = 'completed'
  AND (st.payload -> 'foxcoin_economy' -> 'by_creator' -> ca.creator_id
                  -> 'processed_credits' -> ca.idempotency_key) IS NULL
ORDER BY ca.created_at DESC;

ROLLBACK;
