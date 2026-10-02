import time

import pytest

from memebot.bot import CalloutBot
from memebot.charts import render_analysis_chart
from memebot.config import Settings
from memebot.http import HttpClient
from memebot.scoring import SmartMoney, evaluate
from memebot.sources.bubblemaps import ClusterReport, parse_map
from memebot.sources.dexscreener import parse_pair
from memebot.sources.rugcheck import ContractReport, parse_rugcheck
from memebot.sources.socials import SocialReport, _handle, parse_telegram_members
from memebot.storage import Storage
from memebot.wallets import TrackedWallet, extract_trades

MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
FRIEND = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"


def make_pair(**overrides):
    pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "url": f"https://dexscreener.com/solana/{MINT}",
        "pairAddress": "PAIR",
        "baseToken": {"address": MINT, "name": "Test Dog", "symbol": "TDOG"},
        "priceUsd": "0.0012",
        "liquidity": {"usd": 180_000},
        "marketCap": 1_200_000,
        "volume": {"h24": 900_000, "h1": 60_000},
        "priceChange": {"m5": 2, "h1": 25, "h6": 60, "h24": 140},
        "txns": {"h1": {"buys": 420, "sells": 210}, "h24": {"buys": 5000, "sells": 3500}},
        "pairCreatedAt": (time.time() - 30 * 3600) * 1000,
        "info": {
            "websites": [{"label": "Website", "url": "https://tdog.example"}],
            "socials": [
                {"type": "twitter", "url": "https://x.com/tdog_sol"},
                {"type": "telegram", "url": "https://t.me/tdogportal"},
            ],
        },
    }
    pair.update(overrides)
    return pair


BUBBLEMAP = {
    "decentralization_score": 78,
    "nodes": {
        "top_holders": [
            {"address": "POOL", "holder_data": {"share": 0.18}, "address_details": {"is_contract": True}},
            {"address": "A1", "holder_data": {"share": 0.03}, "address_details": {}},
            {"address": "A2", "holder_data": {"share": 0.02}, "address_details": {}},
            {"address": "A3", "holder_data": {"share": 0.015}, "address_details": {}},
            {"address": "A4", "holder_data": {"share": 0.01}, "address_details": {}},
        ]
    },
    "clusters": [{"share": 0.035, "holder_count": 3, "holders": ["A2", "A3", "A4"]}],
}


class FakeHttp(HttpClient):
    def __init__(self, routes):
        super().__init__(retries=0)
        self.routes = routes

    def _match(self, url):
        for key, value in self.routes.items():
            if key in url:
                return value(url) if callable(value) else value
        return None

    def get_json(self, url, params=None, headers=None):
        return self._match(url)

    def get_text(self, url):
        return self._match(url)

    def is_reachable(self, url):
        return True

    def post_json(self, url, payload, headers=None):
        method = payload["method"]
        result = {
            "getAccountInfo": {"value": {"data": {"parsed": {"info": {"mintAuthority": None, "freezeAuthority": None}}}}},
            "getSignaturesForAddress": [],
        }.get(method)
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}


def test_parse_pair_extracts_market_and_socials():
    m = parse_pair(make_pair())
    assert m.symbol == "TDOG" and m.liquidity_usd == 180_000
    assert 29 < m.age_hours < 31
    assert m.websites == ["https://tdog.example"]
    assert len(m.socials) == 2


def test_parse_map_excludes_contracts_and_counts_connected_wallets():
    report = parse_map(MINT, BUBBLEMAP)
    assert report.connected_wallets == 3
    assert report.cluster_count == 1
    assert report.connected_share == pytest.approx(0.035)
    assert report.top10_share == pytest.approx(0.075)  # Pool ausgeschlossen
    assert "v2.bubblemaps.io" in report.map_url


def test_parse_map_builds_clusters_from_relationships():
    data = {
        "nodes": BUBBLEMAP["nodes"],
        "relationships": [
            {"from_address": "A1", "to_address": "A2"},
            {"from_address": "A2", "to_address": "A3"},
            {"from_address": "POOL", "to_address": "A4"},
        ],
    }
    report = parse_map(MINT, data)
    assert report.connected_wallets == 3
    assert report.largest_cluster_share == pytest.approx(0.065)


def test_telegram_and_handles():
    html = '<div class="tgme_page_extra">12 345 members, 400 online</div>'
    assert parse_telegram_members(html) == 12345
    assert _handle("https://x.com/tdog_sol/status/1") == "tdog_sol"
    assert _handle("https://twitter.com/i/communities/123") is None


def test_rugcheck_detects_authorities():
    data = {"score_normalised": 12, "risks": [{"name": "Freeze Authority still enabled", "level": "danger"}]}
    report = parse_rugcheck(data, ContractReport())
    assert report.freeze_authority is True
    assert report.dangers == ["Freeze Authority still enabled"]


def test_extract_trades_detects_buy_and_sol_spent():
    wallet = TrackedWallet(WALLET, "whale1", "gross")
    tx = {
        "blockTime": 1_700_000_000,
        "transaction": {"message": {"accountKeys": [{"pubkey": WALLET}, {"pubkey": "X"}]}},
        "meta": {
            "err": None,
            "preBalances": [5_000_000_000, 0],
            "postBalances": [2_000_000_000, 0],
            "preTokenBalances": [],
            "postTokenBalances": [
                {"mint": MINT, "owner": WALLET, "uiTokenAmount": {"uiAmount": 250000.0}},
                {"mint": MINT, "owner": "SOMEONE", "uiTokenAmount": {"uiAmount": 1.0}},
            ],
        },
    }
    [trade] = extract_trades(tx, wallet, "SIG")
    assert trade.side == "BUY" and trade.amount == 250000 and trade.sol == pytest.approx(3.0)


def _evaluate(market, clusters, contract, socials, smart=None):
    return evaluate(market, clusters, contract, socials, smart or SmartMoney(),
                    min_liquidity=10_000, max_risk=50, min_score=60)


def test_good_coin_is_called_out_and_rug_is_not():
    socials = SocialReport(twitter_handle="tdog", twitter_followers=8000, telegram_handle="tdog",
                           telegram_members=3000, website="https://tdog.example", website_ok=True)
    good = _evaluate(parse_pair(make_pair()), parse_map(MINT, BUBBLEMAP),
                     ContractReport(mint_authority=False, freeze_authority=False, rugcheck_score=5),
                     socials, SmartMoney(gross_buyers=1))
    assert good.is_callout, good.reasons
    assert good.risk < 40

    bundled = ClusterReport(source="bubblemaps", map_url="", decentralization_score=10, connected_wallets=40,
                            cluster_count=2, largest_cluster_share=0.45, connected_share=0.6, top10_share=0.7)
    rug = _evaluate(parse_pair(make_pair()), bundled, ContractReport(mint_authority=True, freeze_authority=True),
                    SocialReport())
    assert not rug.is_callout
    assert rug.risk_level in ("hoch", "sehr hoch")
    assert any("Mint-Authority" in r for r in rug.reasons)


def test_horizon_fresh_pump_is_short_mature_coin_is_long():
    contract = ContractReport(mint_authority=False, freeze_authority=False, rugcheck_score=5)
    socials = SocialReport(twitter_handle="x", telegram_handle="y", website_ok=True)
    fresh = parse_pair(make_pair(pairCreatedAt=(time.time() - 1800) * 1000,
                                 priceChange={"h1": 80, "h24": 80},
                                 volume={"h24": 2_000_000, "h1": 900_000}))
    mature = parse_pair(make_pair(pairCreatedAt=(time.time() - 20 * 86400) * 1000,
                                  priceChange={"h1": 1, "h24": 5},
                                  volume={"h24": 100_000, "h1": 4_000},
                                  txns={"h1": {"buys": 50, "sells": 50}}))
    clusters = parse_map(MINT, BUBBLEMAP)
    assert _evaluate(fresh, clusters, contract, socials).horizon == "KURZ"
    assert _evaluate(mature, clusters, contract, socials, SmartMoney(gross_buyers=2)).horizon == "LANG"


def test_chart_is_rendered(tmp_path):
    analysis = _evaluate(parse_pair(make_pair()), parse_map(MINT, BUBBLEMAP),
                         ContractReport(mint_authority=False, freeze_authority=False), SocialReport())
    path = render_analysis_chart(analysis, str(tmp_path))
    with open(path, "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"


def test_scan_once_sends_callout_end_to_end(tmp_path):
    http = FakeHttp({
        "token-profiles/latest": [{"chainId": "solana", "tokenAddress": MINT}, {"chainId": "base", "tokenAddress": "0x1"}],
        "token-boosts": [],
        "/tokens/v1/solana/": [make_pair()],
        "api.bubblemaps.io": BUBBLEMAP,
        "rugcheck": {"score_normalised": 3, "risks": []},
        "t.me/": "<div>5 000 members</div>",
    })
    settings = Settings(bubblemaps_api_key="key", chart_dir=str(tmp_path), wallets_file=str(tmp_path / "w.json"),
                        min_score=55)
    bot = CalloutBot(settings, http=http, storage=Storage(":memory:"))
    sent = []
    bot.notifier.send = lambda title, text, **kw: sent.append((title, text, kw)) or ["test"]

    callouts = bot.scan_once()
    assert [c.market.symbol for c in callouts] == ["TDOG"]
    title, text, kw = sent[0]
    assert "$TDOG" in title and "verbundene Wallets" in text
    assert kw["image_path"].endswith(".png") and "CALLOUT" in kw["sms_text"]
    # Cooldown: derselbe Coin wird nicht erneut gemeldet
    assert bot.scan_once() == []


def test_wallet_add_and_hot_tokens(tmp_path):
    settings = Settings(wallets_file=str(tmp_path / "w.json"))
    bot = CalloutBot(settings, http=FakeHttp({}), storage=Storage(":memory:"))
    bot.tracker.add(WALLET, "whale1", "gross")
    bot.tracker.add(FRIEND, "degen1", "klein")
    with pytest.raises(ValueError):
        bot.tracker.add("kein-wallet", "x", "gross")
    assert [w.category for w in bot.tracker.wallets] == ["gross", "klein"]

    now = time.time()
    for wallet in bot.tracker.wallets:
        tx = {"blockTime": now, "transaction": {"message": {"accountKeys": []}},
              "meta": {"err": None, "preTokenBalances": [],
                       "postTokenBalances": [{"mint": MINT, "owner": wallet.address, "uiTokenAmount": {"uiAmount": 10}}]}}
        for trade in extract_trades(tx, wallet, f"sig-{wallet.label}"):
            bot.storage.add_trade(trade)
    [hot] = bot.storage.hot_tokens()
    assert hot["mint"] == MINT and hot["gross"] == 1 and hot["klein"] == 1
    smart = bot.smart_money(MINT)
    assert (smart.gross_buyers, smart.klein_buyers) == (1, 1)
