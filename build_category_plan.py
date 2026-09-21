"""Japonya kategori agacini gezip her kategori/bant kombinasyonu icin
'harcamaya deger mi' kontrolu yapar, sonucu keepa_kategori_plani.json'a yazar.
Bu SADECE PLAN cikarir -- gercek ASIN cekimi (fetch_category_plan_asins)
ayri bir adimdir. CALISTIRMA: python build_category_plan.py
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import keepa_finder as kf


def progress(msg):
    print(msg, flush=True)


def main():
    api_key = kf.load_api_key()
    # save_callback: her derinlik seviyesi bitince ara-kayit -- beklenmeyen
    # bir hata TUM calismayi cokertse bile o ana kadarki (token harcanmis)
    # ilerleme diskte kalsin (canli yasandi: 405 hatasi 3 derinlik
    # seviyesini sildi, 2026-09-14).
    plan = kf.build_category_tree_plan(api_key, progress=progress, save_callback=kf.save_category_plan)
    kf.save_category_plan(plan)
    print(f"\n>>> {len(plan)} kategori/bant kombinasyonu keepa_kategori_plani.json'a kaydedildi.")

    total_est = sum(p.get("total_results") or 0 for p in plan)
    print(f">>> Tum kombinasyonlarin toplam tahmini urun sayisi: {total_est}")


if __name__ == "__main__":
    main()
