"""Find a cohort by keywords and filters, summarize it, save it and plan its download (no transfer)."""

from __future__ import annotations

import argparse

import sda_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="*", default=["glioblastoma"])
    parser.add_argument("--organism", default="human")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--with-image", action="store_true")
    parser.add_argument("--output", default="cohort.json")
    args = parser.parse_args()
    catalog = sda_db.open_catalog()
    filters = {"organism": args.organism}
    if args.platform:
        filters["platform"] = args.platform
    if args.with_image:
        filters["has_image"] = True
    cohort = catalog.search(" ".join(args.query), **filters)
    for warning in cohort.warnings:
        print("note:", warning)
    print(cohort)
    for study in cohort.list_studies()[:10]:
        print(f"{study['published_gse']:>10}  {study['samples']:>4} samples  {', '.join(study['platforms'])}  "
              f"{(study['title'] or '')[:70]}")
    print(cohort.summary(by="split")["groups"])
    cohort.save_selection(args.output)
    print(f"saved {len(cohort)} samples to {args.output}")
    plan = cohort.plan_download(include_image=args.with_image)  # sizes need approved access
    print("download size:", plan.to_dict(per_sample=False)["total"], *plan.notes, sep="\n  ")


if __name__ == "__main__":
    main()
