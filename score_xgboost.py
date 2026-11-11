            f"[DUUR] {duur}"
        )

        if df_top.empty:

            print(
                "[INFO] Geen nieuwe "
                "TOP-resultaten."
            )

        if (
            TELEGRAM_TOKEN
            and TELEGRAM_CHAT_ID
        ):

            print(
                "[INFO] Telegram status: "
                + (
                    "OK"
                    if telegram_ok
                    else "niet verzonden"
                )
            )

        if (
            EMAIL_USER
            and EMAIL_PASS
            and EMAIL_RECEIVER
        ):

            print(
                "[INFO] E-mail status: "
                + (
                    "OK"
                    if email_ok
                    else "niet verzonden"
                )
            )

        return 0

    except Exception as exc:

        print("")
        print("=" * 70)
        print(
            "❌ XGBOOSTV3 SCORING MISLUKT"
        )
        print("=" * 70)

        print(
            f"Type fout : "
            f"{type(exc).__name__}"
        )

        print(
            f"Fout      : {exc}"
        )

        print("")
        print(
            "VOLLEDIGE TRACEBACK:"
        )

        traceback.print_exc()

        if conn is not None:

            try:

                registreer_run(
                    conn,
                    schema,
                    status="fout",
                    aantal_selecties=len(
                        df_selecties
                    ),
                    aantal_scores=opgeslagen,
                    foutmelding=str(
                        exc
                    )[:2000],
                    modellen=modellen,
                )

            except Exception as run_exc:

                print(
                    "[RUN] "
                    "Foutregistratie mislukt: "
                    f"{run_exc}"
                )

        return 1

    finally:

        if conn is not None:

            try:

                conn.close()

                print(
                    "[DB] Verbinding gesloten."
                )

            except Exception as exc:

                print(
                    "[DB] Sluiten verbinding "
                    f"mislukt: {exc}"
                )


# ============================================================
# PROGRAMMA START HIER
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
)
