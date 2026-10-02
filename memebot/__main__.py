"""Kommandozeile: python -m memebot <befehl>"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from .bot import CalloutBot
from .charts import render_analysis_chart
from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="memebot", description="Solana Memecoin Callout Bot")
    parser.add_argument("--env", help="Pfad zur .env Datei", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="Bot dauerhaft laufen lassen (Scan + Wallet-Tracking)")
    sub.add_parser("scan", help="Einmal nach neuen Coins scannen")
    p_an = sub.add_parser("analyze", help="Einen Coin analysieren und Diagramm erstellen")
    p_an.add_argument("mint", help="Token-Adresse (CA)")
    p_an.add_argument("--notify", action="store_true", help="Ergebnis aufs Handy schicken")
    sub.add_parser("test-notify", help="Test-Benachrichtigung senden")

    p_w = sub.add_parser("wallets", help="Wallet-Tracking")
    wsub = p_w.add_subparsers(dest="wcmd", required=True)
    p_add = wsub.add_parser("add", help="Wallet hinzufügen")
    p_add.add_argument("address")
    p_add.add_argument("--label", default="")
    p_add.add_argument("--category", choices=["gross", "klein"], default="klein")
    wsub.add_parser("list", help="Getrackte Wallets anzeigen")
    wsub.add_parser("poll", help="Neue Trades laden")
    wsub.add_parser("report", help="Letzte Trades und heiße Coins anzeigen")
    p_disc = wsub.add_parser("discover", help="Top-Holder eines Coins als Trader übernehmen")
    p_disc.add_argument("mint")
    p_disc.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    bot = CalloutBot(Settings.from_env(args.env))

    if args.cmd == "run":
        bot.run_forever()
    elif args.cmd == "scan":
        callouts = bot.scan_once()
        print(f"{len(callouts)} Callout(s) gesendet.")
    elif args.cmd == "analyze":
        analysis = bot.analyze(args.mint)
        if analysis is None:
            raise SystemExit("Coin nicht gefunden.")
        title, text, _ = bot.callout_text(analysis)
        print(title, text, sep="\n")
        print("\nRisikofaktoren:")
        for f in analysis.risk_factors:
            print(f"  {f.name:<22} {f.value:5.0f}  {f.detail}")
        print("Callout:", "JA" if analysis.is_callout else "nein – " + "; ".join(analysis.reasons))
        if args.notify:
            print("Gesendet über:", bot.send_callout(analysis) or "keinen Kanal")
        else:
            print("Diagramm:", render_analysis_chart(analysis, bot.s.chart_dir))
    elif args.cmd == "test-notify":
        delivered = bot.notifier.send("Memebot Test", "Benachrichtigungen funktionieren ✅",
                                      sms_text="Memebot Test: Benachrichtigungen funktionieren")
        print("Gesendet über:", delivered or "keinen Kanal – .env prüfen")
    elif args.cmd == "wallets":
        _wallets(bot, args)


def _wallets(bot: CalloutBot, args) -> None:
    if args.wcmd == "add":
        wallet = bot.tracker.add(args.address, args.label, args.category)
        print(f"Hinzugefügt: {wallet.label} ({wallet.category})")
    elif args.wcmd == "list":
        for w in bot.tracker.wallets:
            print(f"{w.category:<6} {w.label:<24} {w.address}")
    elif args.wcmd == "poll":
        trades = bot.track_wallets_once()
        print(f"{len(trades)} neue Trades.")
    elif args.wcmd == "report":
        print("Letzte Trades (24h):")
        for t in bot.storage.recent_trades():
            when = datetime.fromtimestamp(t["ts"]).strftime("%d.%m %H:%M")
            print(f"  {when} {t['category']:<6} {t['label']:<20} {t['side']:<4} "
                  f"{t['sol']:8.2f} SOL  {t['mint']}")
        print("\nCoins, die mehrere getrackte Wallets kaufen (6h):")
        for row in bot.storage.hot_tokens():
            print(f"  {row['mint']}  {row['wallets']} Wallets (gross {row['gross']}, klein {row['klein']})")
    elif args.wcmd == "discover":
        market = bot.dex.get_markets([args.mint]).get(args.mint)
        if market is None:
            raise SystemExit("Coin nicht gefunden.")
        report = bot.bubblemaps.analyze(args.mint)
        added = bot.tracker.discover_from_holders(
            report.holders, market.market_cap, market.symbol, bot.s.whale_usd_threshold, args.limit
        )
        for w in added:
            print(f"+ {w.category:<6} {w.label:<24} {w.address}")
        print(f"{len(added)} Wallets hinzugefügt.")


if __name__ == "__main__":
    main()
