"""Runtime configuration for TheHouse.

Two profiles:
- dev (default): SQLite file DB + in-process fakeredis — zero external dependencies.
- prod: Postgres (asyncpg) + real Redis, via env vars.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="THEHOUSE_", env_file=".env", extra="ignore")

    # "dev" → sqlite+fakeredis, "prod" → postgres+redis
    profile: str = "dev"

    database_url: str = "sqlite+aiosqlite:///./thehouse.db"
    redis_url: str = "redis://localhost:6379/0"

    # Economics defaults (overridable per ASP in the registry)
    discount_rate: float = 0.80          # BATCHED price: caller pays target_price × discount_rate
    solo_discount_rate: float = 0.001    # SOLO price: full − 0.1% (batch never formed; still beats
                                         # going direct, so routing through TheHouse always wins)
    min_batch_for_discount: int = 2      # ≥2 payers sharing a call earns the 20% (one dispatch, two
                                         # payers already beats two direct calls). break_even_batch_
                                         # size is a *firing* control, NOT this pricing threshold.
    priority_discount_abs: float = 0.01  # priority callers pay original − $0.01 (fires solo)
    coordination_fee: float = 0.05       # fan-out route: caller pays original × (1 + fee), no discount
    default_window_timer_ms: int = 300
    default_max_batch_size: int = 2      # owner decision: max 2 questions per compound call;
                                         # overflow rolls into further batches to the same target
    default_break_even_batch_size: int = 2   # see QUESTIONS.md Q7
    default_cache_ttl_seconds: int = 30

    # Dispatcher
    dispatch_timeout_ms: int = 10_000

    # Prod-only guard for the unpaid REST intake (/v1/call, /v1/queue): callers must use
    # the paid /mcp gateway; operators authenticate with X-Internal-Token = this value.
    internal_api_token: str = ""

    # Backpressure (refused before any charge; 0 disables a limit)
    rate_limit_per_minute: int = 240   # per caller, across all targets
    max_queue_depth: int = 500         # per target ASP window queue

    # Requests still undelivered after this long fail loudly (status=failed + audit);
    # startup reconciliation re-queues paid work lost to a crash first.
    request_ttl_s: int = 600

    # Semantic dedup (Phase 8)
    overlap_threshold: float = 0.92
    merge_threshold: float = 0.98

    # OKX x402 facilitator (the real settlement rail; see docs/xlayer_integration.md).
    # Credentials reuse the OKX developer keys (OK-ACCESS-*). Left blank in dev → the
    # facilitator client is not constructed and TheHouse stays on the Dev verifier/signer.
    facilitator_base_url: str = "https://web3.okx.com"
    facilitator_path_prefix: str = "/api/v6/pay/x402"
    facilitator_network: str = "eip155:196"     # X Layer mainnet; "eip155:1952" = testnet
    # Live-verified schemes on X Layer (docs/xlayer_integration.md §3b): "exact" (one-shot),
    # "aggr_deferred" (batch many→one settlement), "upto" (authorize ceiling, settle actual ≤ =
    # Model B), "period" (recurring). Batching + settle-below-ceiling combines aggr_deferred + upto.
    facilitator_scheme: str = "aggr_deferred"
    facilitator_upto_address: str = "0x40817a0d9043732d48823c05ab2ffb643ef8d90a"
    facilitator_pay_to: str = ""                # TheHouse's receiving wallet (seller payTo)
    # How the fire-time tier is settled against the intake authorization (Model B):
    #   "deferred_below_ceiling" (default) — intake authorizes the ceiling; the facilitator settles
    #       the actual (lower) tier ≤ ceiling. Requires settle-below-authorized on X Layer.
    #   "charge_at_fire" — fallback if the facilitator rejects partial settlement: the buyer's
    #       session key signs the EXACT tier amount at fire (no human round-trip in the agent
    #       model), so every settlement equals its authorization. Confirm which the live
    #       facilitator supports on the sandbox before mainnet.
    settlement_mode: str = "deferred_below_ceiling"
    # Which rail actually moves money. "a2a_pay" is the PROVEN live rail (X Layer mainnet, EIP-3009
    # charge); "x402_facilitator" is the raw verify/settle path (seller-gated — see
    # docs/xlayer_integration.md). Default a2a_pay now that it's on-chain-validated.
    settlement_rail: str = "a2a_pay"
    settlement_token_symbol: str = "USDT"        # charge/settle asset symbol
    settlement_chain: str = "xlayer"             # onchainos chain alias (xlayer | xlayer_test)
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""


settings = Settings()
