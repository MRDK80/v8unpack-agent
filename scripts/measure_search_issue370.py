"""Pinned offline search evaluation for #370; no runtime changes.

Derived from measure_370_v2.py (recorded SHA-256 below). The measured success
path and metric helpers are retained; error journals, CLI and guards are new.
The source runtime is always the pinned commit, not the caller's worktree.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import platform
import signal
import socket
import statistics
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

SHA = "7124fda57aa1f1b5fbc34fda56082f711c273754"

PROTOCOL_COMMENT = 6234319876

HASHES = {
    "search_eval_issue321.json": "35166f3e74ca7047731fc05a1cf3ed564530a02cf65cf03f2371d3ff48efa052",
    "search_eval_issue321_hard.json": "f94c3d222eb34d2c9b629a3c51379fc7661f42dd252388b1b596da5026b70ec3",
    "search_eval_issue329_paraphrase.json": "5b69e413527a7fc733c525c997a8031310a538fcaa3f584a32b3b31881eee46b",
}

DIGEST = "5e5d945a46bf79b9009d16173cae344f5fd59c4cc61272e3bc4f43e2573ee7d7"

WEIGHTS_SHA256 = "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38"

WEIGHTS_BYTES = 2271145830

THRESHOLDS = {"recall@5": 0.80, "mrr@10": 0.60}
ORIGINAL_SCRIPT_SHA256 = 'fd5de5e102de633e97c22e88015dfe039186428ee383c3a86a11c9168224329c'
SOURCE_HASHES = {'scripts/measure_search_issue357.py': 'b283fe2f7b4ce2a84e61db5975d0d1b239cb63ed339f3c43205ac81987d5d240', 'tests/test_search_eval_issue329_paraphrase.py': '56d085fd961028f7bb3b1963b1fed4b6e0ccb48de463fa77c9f226e23cbb324d', 'v8unpack_agent/__init__.py': '2006ddcd267c38c16b0bdf42eaab6a75da87dce46b568465769fd2cac232395c', 'v8unpack_agent/_exact_names.py': '52000119d3e65fdd91cfee42b4edb7312f576a9cdfaac34b77027daf58f8a7c0', 'v8unpack_agent/_safe_paths.py': '4323d68437737433c06577577bd9a1edca3939974d4cd580f45ac3ba242b877e', 'v8unpack_agent/catalog_resolver.py': '84b1709ba7fd68ba213b14fab06e6a47528021436b1f0da918b00014aec343eb', 'v8unpack_agent/chain_data_path.py': 'f02891ba9fbfe930184552fe9bfa8fd85a6e675efc879d9c009c005f89538088', 'v8unpack_agent/command_modules.py': '7782b65ac40dbeb5a377b3cb48cd09f5cfaf6966753b680a1dd3eca7cee44f0f', 'v8unpack_agent/common_modules.py': '472b92a083474d51c6a5c11f0312cc4d8fe7e61ba75ff8838aebdae6a94502a4', 'v8unpack_agent/configuration_modules.py': '0c597abf40e2c095d57704a7b06f468b222e2efcc106d8b7836388d31605d05d', 'v8unpack_agent/coverage_metric.py': '1f2520e50f3ea831ab0b73532dd91eb4cd114d03b0adc33b3cfb83ca3e385919', 'v8unpack_agent/elem_parser.py': '75c9d423d78264f0abf852edecbf29ed8478f4d1a486918cd5282a672fdf6f3b', 'v8unpack_agent/external_object_modules.py': '09cf7c62fea61f9e525a8799f94c3770b06a50996da3d5fde0695436851f34ff', 'v8unpack_agent/form_context.py': '6ca71909169a83b8ecbf4a35c3dba006e49f6cec70334d6c35a4e2e8bf90dca3', 'v8unpack_agent/form_identity.py': '5aa5467b034d0c41f0c44fab6edc4658cfb9afa5fed8a605b0c9725c554df71e', 'v8unpack_agent/form_paths.py': '8d05f368b8a175e08f7ff54792e1226232c8aebb5cec3cfb8a62fce4a3459a32', 'v8unpack_agent/form_summary.py': 'f1e2b269647f156aadb40ef1cbee889432359a6db4408ed56397f1f3e980792c', 'v8unpack_agent/metadata_modules.py': '35c612601ec78465d9fa06442a566bcfbe9ae858edc3da4f174015e20848b4e0', 'v8unpack_agent/modules.py': '7823aed17c4ecc24cf0881fdce43b9ffec3ad98f50bc9cb6ef9cc4c8004048f1', 'v8unpack_agent/object_decoder.py': 'c531c8ff99a769254cc8ca3cc91e637c25640f43774fd268a6b8026237945914', 'v8unpack_agent/platform_reference_types.py': 'fabca096e057032d10b7235f8272cdb073cc0270599b2fc9123d1c954299d574', 'v8unpack_agent/record_set_modules.py': '026bbce5afa41a19dff30a6042359234b91b12ed1a2fcf057768882e511b285c', 'v8unpack_agent/run_report.py': '741aaf89376eb729b858f262463147f6fb14cabd42ef3fcb1d4a6ec375c2e711', 'v8unpack_agent/runner.py': '700b5d52da2577e969eef3820c5fc4cd3640c2abb2051a585df592cdb9c91510', 'v8unpack_agent/scan_forms.py': 'b5caab72a2ecf876ae1b49cbeb6b4b69bcb8a054821f9e50b937063e01bf131a', 'v8unpack_agent/search_api.py': '7d80e98c71058ba167af64c61cc57f31e3c3e65350aba43e2747ec8e72a65062', 'v8unpack_agent/search_context.py': '50d7627f7cd8cfa645846f9bfb7ec1ef52cfb4694f15b31bb896198e3abd9381', 'v8unpack_agent/search_contract.py': '81b71d390051aacc70741003661dd13d448da77c7244d180986d03b57f33132d', 'v8unpack_agent/search_corpus.py': 'bc2018cb8d0935d6c5d2b2b1bd74d17ac2696a73bbc27bf2f2068f3432cc5104', 'v8unpack_agent/search_dispatch.py': 'dd66c15e5ff7a24795e66f67d94b2f72f0007039b355f02e8d5c3ab98dec569b', 'v8unpack_agent/search_fragments.py': '4b205389e1da1f2be28c6b1cd99269aea3c05bcd1f197bc75f9cc114a95977c9', 'v8unpack_agent/search_index.py': '94d620956f7079e0bdc1594bdad3800fc27b5e0f3ba523d935070487797ac8a7', 'v8unpack_agent/service_modules.py': '8a1640555c3d0bedd71bcd9f4a8245ee212e94d0c9f39fc812b8086626880f13', 'v8unpack_agent/skd_extractor.py': '48a57e99ab52f36a580205a04ac5fb69156f50df31e4c2f91b15afeb05583524', 'v8unpack_agent/value_manager_modules.py': '9ae5794bfad472e66bd228e5d6aa66e2f833a80731631b25ac3f46245ade4959'}
ARCHIVE_PATHS = ['v8unpack_agent', 'scripts/measure_search_issue357.py', 'tests/test_search_eval_issue329_paraphrase.py', 'docs/research/search_eval_issue321.json', 'docs/research/search_eval_issue321_hard.json', 'docs/research/search_eval_issue329_paraphrase.json']


class EvalError(Exception):
    pass

def require(condition, reason):
    if not condition:
        raise EvalError(reason)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    prefix = "configuration:-:Configuration:"
    return "configuration:::" + value[len(prefix):] if value.startswith(prefix) else value

def deny_network(*args, **kwargs):
    raise EvalError("network_forbidden")

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "module_spec_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def origin_report(root):
    rows = []
    for name, module in sorted(sys.modules.items()):
        if name == "v8unpack_agent" or name.startswith(("v8unpack_agent.", "research370_")):
            value = getattr(module, "__file__", None)
            require(value is not None, "module_origin_missing")
            path = Path(value).resolve()
            require(path.is_relative_to(root.resolve()), "module_origin_outside_target")
            rows.append({"module": name, "path": path.relative_to(root.resolve()).as_posix(),
                         "sha256": digest(path.read_bytes())})
    return rows

def inputs(root):
    data, hashes = {}, {}
    for name, expected in HASHES.items():
        raw = (root / "docs" / "research" / name).read_bytes()
        require(digest(raw) == expected, "dataset_hash_mismatch")
        data[name] = json.loads(raw)
        hashes[name] = {"bytes": len(raw), "sha256": digest(raw)}
    projection = (json.dumps(data["search_eval_issue329_paraphrase.json"]["artifacts"],
                  ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    require(digest(projection) == DIGEST, "artifacts_digest_mismatch")
    return data, hashes

def make_corpus(artifacts, api):
    docs, links, entries, mapping = [], [], [], []
    for number, artifact in enumerate(artifacts):
        original_id = artifact["id"]
        text = "\n".join(procedure["text"] for procedure in artifact["procedures"])
        source = artifact.get("source_path", "synthetic/legacy/artifact_" + str(number) + ".bsl")
        if artifact["form_name"] is not None:
            key = artifact.get("form_key")
            if key is None:
                require(original_id.startswith("form:"), "legacy_form_id_missing")
                key = [unquote(part) for part in original_id[5:].split("/")]
            require(len(key) == 4, "invalid_form_key")
            owner = api.OwnerRef(artifact["owner_kind"], artifact["metadata_type"], artifact["owner_name"])
            basis = artifact.get("link_basis", "form_key" if artifact["owner_kind"] == "metadata_object_form" else "unconfirmed")
            installed = owner if basis == "form_key" else None
            ref = api.ArtifactRef.form(*key, owner=installed, source_path=source)
        else:
            configuration = artifact["owner_kind"] == "configuration"
            entry = api.ModuleEntry(
                module_kind=artifact["module_kind"], owner_kind=artifact["owner_kind"],
                metadata_type=None if configuration else artifact["metadata_type"],
                owner_name=None if configuration else artifact["owner_name"],
                relative_path=source, read_status="ok", size_bytes=len(text.encode("utf-8")),
                sha256=digest(text.encode("utf-8")))
            require(api.ModuleEntry.from_dict(entry.to_dict()) == entry, "module_roundtrip_failed")
            entries.append(entry)
            ref = api.ArtifactRef.from_module_entry(entry)
            owner = api.OwnerRef.from_module_entry(entry)
            basis = "module_entry"
        require(ref.artifact_id == canonical(original_id), "artifact_id_mismatch")
        require(api.ArtifactRef.from_dict(ref.to_dict()) == ref, "artifact_roundtrip_failed")
        link = api.OwnerLink(owner=owner, artifact=ref, basis=basis)
        require(api.OwnerLink.from_dict(link.to_dict()) == link, "link_roundtrip_failed")
        links.append(link)
        docs.append(api.SearchDocument(artifact=ref, text=text))
        mapping.append({"original": original_id, "canonical": ref.artifact_id})
    api.ModuleIndex.from_entries(entries)
    documents = api.SearchDocumentSet.from_documents(docs)
    return api.SearchCorpus(documents=documents, owner_links=tuple(links), report=api.CorpusReport()), mapping

def timing(samples, small_sample_note=False):
    require(bool(samples), "missing_timing_samples")
    ordered = sorted(float(value) for value in samples)
    result = {"n": len(ordered), "seconds": [float(v) for v in samples],
              "median_seconds": statistics.median(ordered),
              "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1]}
    if small_sample_note:
        result["sample_note"] = "small_sample_n_3"
    return result

def score_distribution(values):
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None, "values": []}
    ordered = [float(v) for v in values]
    return {"n": len(ordered), "min": min(ordered), "median": statistics.median(ordered),
            "max": max(ordered), "values": ordered}

def score_query(hits, gold, key_fn):
    relevant = {key_fn(canonical(item)) for item in gold}
    require(bool(relevant), "semantic_metrics_require_gold")
    ids = [key_fn(item["artifact_id"]) for item in hits]
    require(len(ids) == len(set(ids)), "duplicate_ranked_artifact")
    relevance = [int(item in relevant) for item in ids[:10]]
    first = next((index + 1 for index, value in enumerate(relevance) if value), None)
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevance))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(10, len(relevant))))
    return {"hit@1": float(bool(relevance and relevance[0])),
            "recall@5": len(set(ids[:5]) & relevant) / len(relevant),
            "mrr@10": 1 / first if first else 0.0, "ndcg@10": dcg / ideal}

def average_metrics(rows):
    if not rows:
        return None
    return {name: statistics.mean(row[name] for row in rows) for name in ("hit@1", "recall@5", "mrr@10", "ndcg@10")}

def group_names_for_query(label, query, artifacts_by_id, key_fn):
    names = ["class:" + query["class"]]
    if query["class"] in ("free", "mixed"):
        names.append("class:free+mixed")
    gold_artifacts = [artifacts_by_id[key_fn(canonical(item))] for item in query["gold"]]
    if any(item["form_name"] is not None for item in gold_artifacts):
        names.append("forms")
    if any(item["large"] for item in gold_artifacts):
        names.append("large")
    for kind in sorted({item["module_kind"] for item in gold_artifacts}):
        names.append("module_kind:" + kind)
    for kind in sorted({item["owner_kind"] for item in gold_artifacts}):
        names.append("owner_kind:" + kind)
    if label == "issue321":
        names.append("tag:" + query.get("tag", "base"))
    else:
        names.extend(["group:" + query["group"], "tag:" + query["tag"]])
        if query.get("lexical_disjoint"):
            names.append("annotation:lexical_disjoint")
        if query.get("tail"):
            names.append("annotation:tail")
        if query.get("distractor") is not None:
            names.append("annotation:distractor")
    return names

def initial_groups(label, artifacts, validator):
    names = ["class:free", "class:free+mixed", "forms", "large"]
    if label == "issue321":
        names.extend(["class:mixed", "tag:base", "tag:paraphrase", "tag:tail", "tag:distractor"])
    else:
        names.extend(["tag:paraphrase", "annotation:lexical_disjoint", "annotation:tail", "annotation:distractor"])
        names.extend("group:" + name for name in validator.GROUPS)
    for kind in ("command", "common_module", "external_connection", "form", "managed_application",
                 "manager", "object", "ordinary_application", "record_set", "service", "session", "value_manager"):
        names.append("module_kind:" + kind)
    for kind in sorted({item["owner_kind"] for item in artifacts} | {"common_command", "common_form"}):
        names.append("owner_kind:" + kind)
    return {name: [] for name in names}

def fragment_diagnostics(queries, responses, fragmentation, key_fn):
    by_doc = {fragment.document.document_id: fragment for fragment in fragmentation.fragments}
    gold_lookup = defaultdict(set)
    for fragment in fragmentation.fragments:
        if fragment.procedure is not None:
            key = key_fn(fragment.document.artifact.artifact_id + "#" + fragment.procedure)
            gold_lookup[key].add(fragment.document.document_id)
    rows, unresolved = [], []
    top1_matches = 0
    best_matches = 0
    evaluated = 0
    for query in queries:
        if not query["gold_fragments"]:
            continue
        evaluated += 1
        expected_docs = set()
        for item in query["gold_fragments"]:
            docs = gold_lookup.get(key_fn(canonical(item)), set())
            if not docs:
                unresolved.append({"query_id": query["id"], "gold_fragment": canonical(item)})
            expected_docs.update(docs)
        hits = responses[query["id"]]["hits"]
        top1_doc = hits[0]["document_id"] if hits else None
        gold_keys = {key_fn(canonical(item)) for item in query["gold"]}
        selected = next((hit for hit in hits if key_fn(hit["artifact_id"]) in gold_keys), None)
        selected_doc = selected["document_id"] if selected else None
        top1_ok = top1_doc in expected_docs
        selected_ok = selected_doc in expected_docs
        top1_matches += int(top1_ok)
        best_matches += int(selected_ok)
        frag = by_doc.get(selected_doc) if selected_doc else None
        rows.append({"query_id": query["id"], "expected_gold_fragments": [canonical(x) for x in query["gold_fragments"]],
                     "resolved_runtime_document_ids": sorted(expected_docs), "top1_document_id": top1_doc,
                     "top1_matches_gold_fragment": top1_ok,
                     "selected_gold_artifact_document_id": selected_doc,
                     "selected_procedure": frag.procedure if frag else None,
                     "selected_span": frag.document.span.to_dict() if frag and frag.document.span else None,
                     "selected_matches_gold_fragment": selected_ok})
    require(not unresolved, "unresolved_gold_fragment")
    return {"evaluated_queries": evaluated, "unresolved": unresolved,
            "top1_fragment_match_rate": top1_matches / evaluated if evaluated else None,
            "selected_artifact_fragment_match_rate": best_matches / evaluated if evaluated else None,
            "per_query": rows}

def summarize_method(label, queries, artifacts, responses, fragmentation, key_fn, validator):
    artifacts_by_id = {key_fn(canonical(item["id"])): item for item in artifacts}
    groups = initial_groups(label, artifacts, validator)
    free_groups = defaultdict(list)
    per_query = []
    intents = defaultdict(list)
    status_counts = Counter()
    context_counts = Counter()
    for query in queries:
        response = responses[query["id"]]
        status_counts[response["search_status"]] += 1
        context_counts[response["context_status"]] += 1
        if query["class"] not in ("free", "mixed"):
            continue
        if response["search_status"] == "error" or response["error"] is not None:
            value = {"hit@1": 0.0, "recall@5": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0}
        else:
            value = score_query(response["hits"], query["gold"], key_fn)
        row = {"id": label + ":" + query["id"], "raw_id": query["id"], "class": query["class"],
               "tag": query.get("tag", "base"), "group": query.get("group"),
               "intent_id": query.get("intent_id"), "search_status": response["search_status"],
               "context_status": response["context_status"], "dispatch_path": response["dispatch_path"],
               "error": response["error"], "gold": [canonical(g) for g in query["gold"]],
               "metrics": value, "top10": response["hits"][:10]}
        per_query.append(row)
        for name in group_names_for_query(label, query, artifacts_by_id, key_fn):
            groups.setdefault(name, []).append(value)
            if query["class"] == "free":
                free_groups[name].append(value)
        if query.get("intent_id"):
            intents[query["intent_id"]].append((query, value))
    summary_groups = {}
    failed_slices = []
    for name, rows in sorted(groups.items()):
        metrics_value = average_metrics(rows)
        free_rows = free_groups.get(name, [])
        free_metrics = average_metrics(free_rows)
        passed = None
        if free_metrics is not None:
            passed = all(free_metrics[k] >= v for k, v in THRESHOLDS.items())
            if not passed:
                failed_slices.append({"slice": name, "n_free": len(free_rows), "free_metrics": free_metrics})
        summary_groups[name] = {"n": len(rows), "metrics": metrics_value if rows else "not measured",
                                "n_free": len(free_rows),
                                "free_metrics": free_metrics if free_rows else "not measured",
                                "free_thresholds_passed": passed}
    intent_rows = {}
    intent_by_group = defaultdict(list)
    for intent_id, items in sorted(intents.items()):
        avg = average_metrics([item[1] for item in items])
        group = items[0][0]["group"]
        passed = all(avg[k] >= v for k, v in THRESHOLDS.items())
        intent_rows[intent_id] = {"group": group, "formulations": len(items),
                                  "query_ids": [item[0]["id"] for item in items],
                                  "metrics": avg, "thresholds_passed": passed}
        intent_by_group[group].append(avg)
    intent_macro = {"overall": {"n_intents": len(intent_rows),
                    "metrics": average_metrics([row["metrics"] for row in intent_rows.values()]) if intent_rows else None},
                    "by_group": {g: {"n_intents": len(vals), "metrics": average_metrics(vals)}
                                 for g, vals in sorted(intent_by_group.items())}}
    negative_rows = []
    negative_scores = []
    for query in queries:
        if query["class"] == "negative":
            response = responses[query["id"]]
            top1 = response["hits"][0]["score"] if response["hits"] else None
            if top1 is not None:
                negative_scores.append(top1)
            negative_rows.append({"id": label + ":" + query["id"], "raw_id": query["id"],
                                  "search_status": response["search_status"],
                                  "top1_artifact_id": response["hits"][0]["artifact_id"] if response["hits"] else None,
                                  "top1_document_id": response["hits"][0]["document_id"] if response["hits"] else None,
                                  "top1_score": top1})
    free_main = summary_groups["class:free"]["free_metrics"]
    return {"thresholds_free": THRESHOLDS,
            "corpus_free_thresholds_passed": bool(isinstance(free_main, dict) and all(free_main[k] >= v for k, v in THRESHOLDS.items())),
            "failed_free_slices": failed_slices, "groups": summary_groups,
            "intent_summary": intent_macro, "intents": intent_rows, "per_query": per_query,
            "status_counts": dict(status_counts), "context_status_counts": dict(context_counts),
            "negative": {"refusal_threshold": None, "distribution": score_distribution(negative_scores),
                         "queries": negative_rows},
            "fragment_diagnostics": fragment_diagnostics(queries, responses, fragmentation, key_fn)}

def rank_bm25(fragments, scores, key_fn):
    require(len(fragments) == len(scores) and all(math.isfinite(float(v)) for v in scores), "invalid_bm25_scores")
    best = {}
    for fragment, score in zip(fragments, scores):
        aid = fragment.document.artifact.artifact_id
        did = fragment.document.document_id
        row = (float(score), did, aid)
        key = key_fn(aid)
        current = best.get(key)
        if current is None or (-row[0], row[1]) < (-current[0], current[1]):
            best[key] = row
    ordered = sorted(best.values(), key=lambda row: (-row[0], row[1]))
    return [{"artifact_id": aid, "document_id": did, "score": score, "method": "bm25"} for score, did, aid in ordered[:10]]

def evaluate_bm25(label, queries, artifacts, fragmentation, historical, validator, key_fn, journal):
    docs = [historical.Document(f.document.artifact.artifact_id, f.document.document_id,
                               f.search_text, len(f.document.text)) for f in fragmentation.fragments]
    historical.BM25(docs).scores(queries[0]["text"])
    builds, scoring_times, total_times = [], [], []
    first_responses = None
    expected_signature = None
    for repeat in range(3):
        start = time.perf_counter()
        engine = historical.BM25(docs)
        builds.append(time.perf_counter() - start)
        current = {}
        for query in queries:
            t0 = time.perf_counter()
            try:
                scores = engine.scores(query["text"])
                t1 = time.perf_counter()
                hits = rank_bm25(fragmentation.fragments, scores, key_fn)
                t2 = time.perf_counter()
                response = {"search_status": "semantic", "context_status": "not_built_research_baseline",
                            "dispatch_path": ["research_bm25"], "error": None, "hits": hits}
            except Exception:  # noqa: BLE001 -- retain failed query and remaining denominator
                t1 = t2 = time.perf_counter()
                response = failed_response("baseline_query_exception", "not_built_research_baseline")
            scoring_times.append(t1 - t0)
            total_times.append(t2 - t0)
            current[query["id"]] = response
            journal.add(label, "bm25", repeat, query["id"], response, t2 - t0)
        signature = response_signature(current)
        if expected_signature is not None and signature != expected_signature:
            journal.event(label, "bm25", "rankings_not_reproducible")
        expected_signature = signature
        if repeat == 0:
            first_responses = current
    summary = summarize_method(label, queries, artifacts, first_responses, fragmentation, key_fn, validator)
    return {"method": "reconstructed_bm25_on_runtime_fragments", "k1": 1.5, "b": 0.75,
            "stem_length": 5, "min_word_length_exclusive": 3,
            "ranking_tie_break": "max_score_per_artifact_then_smallest_document_id",
            "timing_boundary": "research_lexical_only_not_public_context_api",
            "build_timing": timing(builds, small_sample_note=True),
            "lexical_scoring_timing": timing(scoring_times),
            "query_total_timing": timing(total_times), **summary}

def evaluate_exact_controls(label, queries, fragmentation, api, journal):
    key_fn = api.identity_key
    exact_rows, ambiguous_rows, negative_rows = [], [], []
    exact_hits = 0
    ambiguous_hits = 0
    false_negative_exact = 0
    for query in queries:
        if query["class"] not in ("exact", "ambiguous", "negative"):
            continue
        response, seconds = public_attempt(api, fragmentation, query, mode="exact")
        journal.add(label, "exact", 0, query["id"], response, seconds)
        status = response["search_status"]
        found = [hit["artifact_id"] for hit in response["hits"]]
        found_keys = {key_fn(item) for item in found}
        gold = [canonical(item) for item in query["gold"]]
        gold_keys = {key_fn(item) for item in gold}
        row = {"id": label + ":" + query["id"], "raw_id": query["id"], "class": query["class"],
               "text": query["text"], "expect_status": query["expect_status"],
               "runtime_status": status, "context_status": response["context_status"],
               "gold": gold, "found": found, "error": response["error"]}
        if query["class"] == "exact":
            matched = status == "exact" and len(found) == 1 and found_keys == gold_keys
            exact_hits += int(matched)
            row["matched"] = matched
            if query["id"] == "e4":
                row["known_divergence"] = "legacy_synonym_gold_not_exact_by_issue326_contract"
            exact_rows.append(row)
        elif query["class"] == "ambiguous":
            matched = status == "ambiguous" and found_keys == gold_keys and len(found) >= 2
            ambiguous_hits += int(matched)
            row["set_recall"] = len(found_keys & gold_keys) / len(gold_keys) if gold_keys else 0.0
            row["matched"] = matched
            ambiguous_rows.append(row)
        else:
            false_exact = status != "empty" or bool(found)
            false_negative_exact += int(false_exact)
            row["false_exact"] = false_exact
            negative_rows.append(row)
    return {"runtime_executed": True, "search_mode": "exact",
            "exact_total": len(exact_rows), "exact_matched": exact_hits,
            "exact_accuracy_at_1": exact_hits / len(exact_rows) if exact_rows else None,
            "exact_target_accuracy_at_1": 1.0,
            "exact_Without_e4_diagnostic": (sum(r["matched"] for r in exact_rows if r["raw_id"] != "e4") / sum(r["raw_id"] != "e4" for r in exact_rows)) if any(r["raw_id"] != "e4" for r in exact_rows) else None,
            "ambiguous_total": len(ambiguous_rows), "ambiguous_matched": ambiguous_hits,
            "ambiguous_full_set_rate": ambiguous_hits / len(ambiguous_rows) if ambiguous_rows else None,
            "negative_total": len(negative_rows), "negative_false_exact": false_negative_exact,
            "exact_queries": exact_rows, "ambiguous_queries": ambiguous_rows,
            "negative_queries": negative_rows}

class BgeM3RuntimeEmbedder:
    def __init__(self, model, spec, np_module, identity_cls):
        self._model = model
        self._spec = spec
        self._np = np_module
        config_payload = json.dumps({"model": spec["name"], "revision": spec["revision"],
            "pooling": spec["pooling"], "dimension": spec["dimension"], "max_seq_length": spec["max_tokens"],
            "normalize_embeddings": True, "query_prefix": spec["query_prefix"],
            "document_prefix": spec["document_prefix"], "dtype": "float32"},
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._identity = identity_cls(name=spec["name"], version=spec["revision"],
                                      dimension=spec["dimension"], config_hash=digest(config_payload))

    @property
    def identity(self):
        return self._identity

    def embed(self, texts):
        values = self._model.encode(list(texts), batch_size=4, normalize_embeddings=True,
                                    prompt="", show_progress_bar=False, convert_to_numpy=True)
        require(values.shape == (len(texts), self._spec["dimension"]) and self._np.isfinite(values).all(), "invalid_model_vectors")
        norms = self._np.linalg.norm(values, axis=1)
        require(self._np.allclose(norms, 1.0, atol=0.00001), "non_unit_model_vectors")
        return tuple(tuple(float(x) for x in row) for row in values.tolist())

def load_bge_m3(historical, api):
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["MKL_NUM_THREADS"] = "4"
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.manual_seed(0)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    spec = next(item for item in historical.MODEL_SPECS if item["name"] == "BAAI/bge-m3")
    snap = historical.model_snapshot(spec)
    weights = snap / "pytorch_model.bin"
    require(weights.is_file() and weights.stat().st_size == WEIGHTS_BYTES, "weights_missing_or_size_mismatch")
    h = hashlib.sha256()
    with weights.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    require(h.hexdigest() == WEIGHTS_SHA256, "weights_sha256_mismatch")
    snapshot_files = []
    for path in sorted(snap.rglob("*")):
        if path.is_file():
            fh = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1048576), b""):
                    fh.update(chunk)
            snapshot_files.append({"name": path.relative_to(snap).as_posix(),
                                   "bytes": path.stat().st_size, "sha256": fh.hexdigest()})
    start = time.perf_counter()
    model = SentenceTransformer(str(snap), device="cpu", local_files_only=True, trust_remote_code=False)
    model.float()
    model.eval()
    load_seconds = time.perf_counter() - start
    require(model.max_seq_length == spec["max_tokens"] and model.get_sentence_embedding_dimension() == spec["dimension"], "loaded_model_config_mismatch")
    embedder = BgeM3RuntimeEmbedder(model, spec, np, api.EmbedderIdentity)
    metadata = {"model": spec, "device": "cpu", "dtype": "float32", "batch_size": 4,
                "threads": torch.get_num_threads(), "inter_op_threads": torch.get_num_interop_threads(),
                "seed": 0, "normalize_embeddings": True, "load_seconds": load_seconds,
                "parameter_count": sum(p.numel() for p in model.parameters()),
                "embedder_identity": embedder.identity.to_dict(),
                "weights_verified": {"file": "pytorch_model.bin", "bytes": WEIGHTS_BYTES, "sha256": WEIGHTS_SHA256},
                "snapshot_total_bytes": sum(item["bytes"] for item in snapshot_files),
                "snapshot_files": snapshot_files}
    return model, embedder, spec, metadata

def token_truncations(model, max_tokens, ids, values):
    counts = model.tokenizer(values, truncation=False, padding=False, add_special_tokens=True, return_length=True)["length"]
    return [{"id": item_id, "tokens_before_truncation": int(count), "max_tokens": max_tokens}
            for item_id, count in zip(ids, counts) if count > max_tokens]

def extract_public_response(context):
    result = context.dispatch.result
    hits = [{"artifact_id": hit.artifact.artifact_id, "document_id": hit.document_id,
             "score": hit.similarity, "method": hit.method, "matched_field": hit.matched_field}
            for hit in result.hits]
    return {"search_status": result.status, "context_status": context.status,
            "dispatch_path": list(context.dispatch.path),
            "error": None if result.error is None else result.error.to_dict(),
            "hits": hits}

def evaluate_bge_public(label, queries, artifacts, fragmentation, root, model, embedder, spec, api, validator, journal):
    doc_ids = [f.document.document_id for f in fragmentation.fragments]
    doc_texts = [f.search_text for f in fragmentation.fragments]
    query_ids = [q["id"] for q in queries]
    query_texts = [q["text"] for q in queries]
    truncations = {"documents": token_truncations(model, spec["max_tokens"], doc_ids, doc_texts),
                   "queries": token_truncations(model, spec["max_tokens"], query_ids, query_texts)}
    embedder.embed(doc_texts[:1])
    embedder.embed(query_texts[:1])
    builds, loads, query_times = [], [], []
    snapshot_sizes, snapshot_hashes = [], []
    snapshot_path = root / (label + "_bge_m3_snapshot.json")
    loaded = None
    for _ in range(3):
        t0 = time.perf_counter()
        built = api.build_search_snapshot(fragmentation, embedder, snapshot_path)
        builds.append(time.perf_counter() - t0)
        raw = snapshot_path.read_bytes()
        snapshot_sizes.append(len(raw))
        snapshot_hashes.append(digest(raw))
        t1 = time.perf_counter()
        loaded = api.load_index(snapshot_path)
        status = loaded.assess(embedder.identity, fragmentation)
        loads.append(time.perf_counter() - t1)
        require(status.compatibility == "compatible" and status.freshness == "fresh", "snapshot_not_fresh_compatible")
        require(loaded.to_json() == built.to_json(), "snapshot_roundtrip_failed")
    if len(set(snapshot_sizes)) != 1 or len(set(snapshot_hashes)) != 1:
        journal.event(label, "bge_m3", "snapshot_not_reproducible")
    first_responses = None
    expected_signature = None
    for repeat in range(3):
        warm, seconds = public_attempt(api, fragmentation, queries[0], loaded, embedder)
        journal.add(label, "bge_m3_warmup", repeat, queries[0]["id"], warm, seconds)
        current = {}
        for query in queries:
            response, seconds = public_attempt(api, fragmentation, query, loaded, embedder)
            query_times.append(seconds)
            current[query["id"]] = response
            journal.add(label, "bge_m3", repeat, query["id"], response, seconds)
        signature = response_signature(current)
        if expected_signature is not None and signature != expected_signature:
            journal.event(label, "bge_m3", "rankings_not_reproducible")
        expected_signature = signature
        if repeat == 0:
            first_responses = current
    summary = summarize_method(label, queries, artifacts, first_responses, fragmentation, api.identity_key, validator)
    return {"method": "public_search_api_bge_m3", "search_mode": "auto", "top_k": 10,
            "max_chars": -1, "max_tokens": None, "query_cache_used": False, "document_cache_used": False,
            "timing_boundary": "public_build_search_snapshot_load_index_assess_and_search_with_context_excluding_json_serialization",
            "token_truncations": truncations,
            "snapshot": {"schema": loaded.schema, "corpus_schema": loaded.corpus_schema,
                         "representation_version": loaded.representation_version,
                         "entries": len(loaded.entries), "bytes": snapshot_sizes[0],
                         "sha256": snapshot_hashes[0], "compatibility": "compatible", "freshness": "fresh"},
            "snapshot_repeats": {"bytes": snapshot_sizes, "sha256": snapshot_hashes},
            "build_save_timing": timing(builds, small_sample_note=True),
            "load_assess_timing": timing(loads, small_sample_note=True),
            "query_timing": timing(query_times), **summary}

def evaluate_corpus(label, artifacts, queries, root, model, embedder, spec, api, historical, validator, report, mode, journal):
    row = {"corpus": label, "artifacts": len(artifacts), "queries": len(queries), "stage": "corpus_construction"}
    report["corpora"][label] = row
    t0 = time.perf_counter()
    corpus, mapping = make_corpus(artifacts, api)
    t1 = time.perf_counter()
    fragmentation = api.fragment_corpus(corpus, parameters=api.FragmentParameters(max_chars=2000, overlap_lines=3))
    sources = {doc.artifact.artifact_id: doc.text for doc in corpus.documents.documents}
    api.verify_fragmentation(fragmentation, sources)
    t2 = time.perf_counter()
    projection_payload = json.dumps([{"artifact_id": a["id"], "canonical_id": canonical(a["id"]),
                                      "procedures": a["procedures"]} for a in artifacts],
                                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    search_payload = json.dumps([{"document_id": f.document.document_id, "search_text": f.search_text,
                                  "text_sha256": f.document.text_sha256} for f in fragmentation.fragments],
                                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    row.update(fragments=len(fragmentation.fragments),
               representation_version=fragmentation.representation_version,
               corpus_projection_sha256=digest(projection_payload.encode("utf-8")),
               search_inputs_sha256=digest(search_payload.encode("utf-8")),
               id_mappings=[item for item in mapping if item["original"] != item["canonical"]],
               owner_links={"confirmed": sum(link.confirmed for link in corpus.owner_links),
                            "unconfirmed": sum(not link.confirmed for link in corpus.owner_links)},
               module_kinds=dict(Counter(a["module_kind"] for a in artifacts)),
               owner_kinds=dict(Counter(a["owner_kind"] for a in artifacts)),
               query_classes=dict(Counter(q["class"] for q in queries)),
               preparation_timing={"corpus_seconds": t1 - t0, "fragmentation_seconds": t2 - t1})
    row["stage"] = "exact_controls"
    row["exact_controls"] = evaluate_exact_controls(label, queries, fragmentation, api, journal)
    if mode == "smoke":
        row["stage"] = "mechanics_smoke"
        row["smoke"] = smoke_corpus(label, queries, fragmentation, root, api, journal)
    else:
        row["stage"] = "lexical_baseline"
        row["lexical_baseline"] = evaluate_bm25(label, queries, artifacts, fragmentation, historical, validator, api.identity_key, journal)
        if mode == "model":
            row["stage"] = "bge_m3_public"
            row["bge_m3"] = evaluate_bge_public(label, queries, artifacts, fragmentation, root, model, embedder, spec, api, validator, journal)
    row["stage"] = "complete"
    row["status"] = "complete"

def hardware_environment():
    cpu_model = "unknown"
    mem_total_bytes = None
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                mem_total_bytes = int(line.split()[1]) * 1024
                break
    packages = {}
    for name in ("v8unpack", "sentence-transformers", "transformers", "torch", "numpy", "huggingface-hub", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version.split()[0], "os": platform.system(), "os_release": platform.release(),
            "machine": platform.machine(), "cpu_count_logical": os.cpu_count(),
            "cpu_model": cpu_model, "ram_total_bytes": mem_total_bytes, "packages": packages}

def perform(root, report, mode, journal):
    from types import SimpleNamespace

    from v8unpack_agent.modules import ModuleEntry, ModuleIndex
    from v8unpack_agent.search_api import (
        HashedBagOfWordsDemoEmbedder,
        build_search_snapshot,
        search_with_context,
    )
    from v8unpack_agent.search_contract import (
        ArtifactRef,
        OwnerLink,
        OwnerRef,
        SearchDocument,
        SearchDocumentSet,
        SearchQuery,
        identity_key,
    )
    from v8unpack_agent.search_corpus import CorpusReport, SearchCorpus
    from v8unpack_agent.search_fragments import (
        FragmentParameters,
        fragment_corpus,
        verify_fragmentation,
    )
    from v8unpack_agent.search_index import EmbedderIdentity, load_index
    api = SimpleNamespace(ModuleEntry=ModuleEntry, ModuleIndex=ModuleIndex, ArtifactRef=ArtifactRef, OwnerRef=OwnerRef, OwnerLink=OwnerLink, SearchDocument=SearchDocument, SearchDocumentSet=SearchDocumentSet, SearchQuery=SearchQuery, identity_key=identity_key, SearchCorpus=SearchCorpus, CorpusReport=CorpusReport, fragment_corpus=fragment_corpus, FragmentParameters=FragmentParameters, verify_fragmentation=verify_fragmentation, build_search_snapshot=build_search_snapshot, search_with_context=search_with_context, HashedBagOfWordsDemoEmbedder=HashedBagOfWordsDemoEmbedder, EmbedderIdentity=EmbedderIdentity, load_index=load_index)
    historical = load_module("research370_historical", root / "scripts" / "measure_search_issue357.py")
    validator = load_module("research370_validator", root / "tests" / "test_search_eval_issue329_paraphrase.py")
    report["environment"] = hardware_environment()
    report["import_sources"] = origin_report(root)
    datasets, before = inputs(root)
    old = [datasets["search_eval_issue321.json"], datasets["search_eval_issue321_hard.json"]]
    artifacts, queries = historical.validate_sets(old)
    new = datasets["search_eval_issue329_paraphrase.json"]
    validator.validate_dataset(new)
    require(len(artifacts) == 21 and len(queries) == 42, "legacy_counts_changed")
    require(len(new["artifacts"]) == 34 and len(new["queries"]) == 168, "new_counts_changed")
    report["dataset_hashes_before"] = before
    report["planned_attempts"] = planned_attempts({"issue321": queries, "issue369": new["queries"]}, mode)
    model = embedder = spec = None
    if mode == "model":
        report["stage"] = "model_loading"
        model, embedder, spec, model_meta = load_bge_m3(historical, api)
        report["model_loaded"] = True
        report["model_evaluation"] = model_meta
    for label, arts, qs in (("issue321", artifacts, queries), ("issue369", new["artifacts"], new["queries"])):
        report["stage"] = "corpus_" + label
        try:
            evaluate_corpus(label, arts, qs, root, model, embedder, spec, api, historical, validator, report, mode, journal)
        except Exception:  # noqa: BLE001 -- preserve partial corpus diagnostics
            journal.event(label, "corpus", "corpus_execution_failed")
    _, after = inputs(root)
    require(after == before, "dataset_changed_during_run")
    report["dataset_hashes_after"] = after
    report["import_sources"] = origin_report(root)

class QueryJournal:
    """Preserve every attempt, including failures; never publish exception text."""

    def __init__(self):
        self.rows = []
        self.failures = []

    def add(self, corpus, method, repeat, query_id, response, seconds):
        row = {"corpus": corpus, "method": method, "repeat": repeat,
               "query_id": query_id, "response": response, "seconds": seconds}
        self.rows.append(row)
        if response.get("error") is not None or response["search_status"] == "error":
            self.failures.append({"corpus": corpus, "method": method,
                                  "repeat": repeat, "query_id": query_id,
                                  "code": "query_failed"})

    def event(self, corpus, method, code):
        self.failures.append({"corpus": corpus, "method": method, "code": code})


def failed_response(code, context_status="not_built"):
    return {"search_status": "error", "context_status": context_status,
            "dispatch_path": [], "error": {"code": code}, "hits": []}


def public_attempt(api, fragmentation, query, index=None, embedder=None, mode="auto"):
    started = time.perf_counter()
    try:
        context = api.search_with_context(
            fragmentation, query["text"], search_mode=mode, top_k=10,
            filters=None, index=index, embedder=embedder, max_chars=-1,
            max_tokens=None)
        seconds = time.perf_counter() - started
        response = extract_public_response(context)
        if response["context_status"] != "built" and response["error"] is None:
            response["error"] = {"code": "context_not_built"}
        return response, seconds
    except Exception:  # noqa: BLE001 -- isolate one query, never leak exception details
        return failed_response("query_exception"), time.perf_counter() - started


def response_signature(current):
    return {qid: (val["search_status"], val["context_status"], tuple(val["dispatch_path"]),
                  tuple((h["artifact_id"], h["document_id"],
                         None if h["score"] is None else round(h["score"], 12))
                        for h in val["hits"])) for qid, val in current.items()}

def smoke_corpus(label, queries, fragmentation, root, api, journal):
    embedder = api.HashedBagOfWordsDemoEmbedder()
    path = root / (label + "_smoke_snapshot.json")
    built = api.build_search_snapshot(fragmentation, embedder, path)
    loaded = api.load_index(path)
    status = loaded.assess(embedder.identity, fragmentation)
    require(status.compatibility == "compatible" and status.freshness == "fresh", "snapshot_not_fresh_compatible")
    require(built.to_json() == loaded.to_json(), "snapshot_roundtrip_failed")
    procedure_keys = {api.identity_key(f.document.artifact.artifact_id + "#" + f.procedure)
                      for f in fragmentation.fragments if f.procedure is not None}
    artifact_keys = {f.document.artifact.identity for f in fragmentation.fragments}
    for query in queries:
        require(all(api.identity_key(canonical(g)) in artifact_keys for g in query["gold"]), "unresolved_gold_artifact")
        require(all(api.identity_key(canonical(g)) in procedure_keys for g in query["gold_fragments"]), "unresolved_gold_procedure")
        response, seconds = public_attempt(api, fragmentation, query, loaded, embedder)
        journal.add(label, "smoke", 0, query["id"], response, seconds)
    return {"quality_measured": False, "queries_attempted": len(queries),
            "snapshot_status": status.to_dict(), "snapshot_bytes": path.stat().st_size}

def planned_attempts(corpora, mode):
    rows = []
    for label, queries in corpora.items():
        for query in queries:
            if query["class"] in ("exact", "ambiguous", "negative"):
                rows.append([label, "exact", 0, query["id"]])
            methods = ("smoke",) if mode == "smoke" else (("bm25", "bge_m3") if mode == "model" else ("bm25",))
            for method in methods:
                for repeat in range(1 if mode == "smoke" else 3):
                    rows.append([label, method, repeat, query["id"]])
    return rows


def save_json(path, report):
    """Atomic create without overwriting an existing result, including races."""
    require(not path.exists() and not path.is_symlink(), "output_exists")
    require(path.parent.is_dir(), "output_directory_missing")
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".research370-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_source_tree(root):
    for name, expected in SOURCE_HASHES.items():
        path = root / name
        require(path.is_file() and not path.is_symlink(), "source_missing")
        require(digest(path.read_bytes()) == expected, "runtime_source_mismatch")


def run_worker(root, result_path, sites, mode):
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root)] + sites
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    report = {"schema": "search_quality_issue370_worker/2", "source_sha": SHA,
              "protocol_comment": PROTOCOL_COMMENT, "mode": mode,
              "quality_measured": False, "model_loaded": False, "network_used": False,
              "corpora": {}, "stage": "imports", "planned_attempts": []}
    journal = QueryJournal()
    started = time.perf_counter()
    report.update(status="execution_failed", rc=1)
    try:
        verify_source_tree(root)
        perform(root, report, mode, journal)
        report.update(stage="complete")
    except Exception as exc:  # noqa: BLE001 -- sanitized worker boundary
        report["error"] = str(exc) if isinstance(exc, EvalError) else type(exc).__name__
    finally:
        report["attempts"] = journal.rows
        report["execution_failures"] = journal.failures
        done = {(r["corpus"], r["method"], r["repeat"], r["query_id"]) for r in journal.rows}
        report["not_run"] = [row for row in report["planned_attempts"] if tuple(row) not in done]
        if report.get("dataset_hashes_before"):
            try:
                _, after = inputs(root)
                report["dataset_hashes_after"] = after
                require(after == report["dataset_hashes_before"], "dataset_changed_during_run")
            except Exception:  # noqa: BLE001 -- failure is recorded independently
                report["error"] = "final_dataset_check_failed"
    if report["stage"] == "complete" and not report.get("error") and not journal.failures and not report["not_run"]:
        report.update(status={"smoke": "smoke_ok", "baseline": "baseline_complete", "model": "evaluation_complete"}[mode],
                      rc=0, quality_measured=mode != "smoke")
    report["duration_seconds"] = time.perf_counter() - started
    report["END"] = "search_quality_issue370_worker/2"
    save_json(result_path, report)
    return report["rc"]


def git(repo, *args):
    proc = subprocess.run(["git", "--no-pager", "-C", str(repo), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, check=False)
    require(proc.returncode == 0, "git_" + args[0] + "_failed")
    return proc.stdout


def extract_tree(raw, target):
    allowed_files = {p for p in ARCHIVE_PATHS if p != "v8unpack_agent"}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and chr(92) not in member.name, "unsafe_archive_path")
            require(member.isfile() or member.isdir(), "special_archive_member")
            allowed = member.isdir() or (path.parts and path.parts[0] == "v8unpack_agent") or member.name in allowed_files
            require(allowed, "unexpected_archive_member")
            dest = target.joinpath(*path.parts)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                require(stream is not None, "archive_data_missing")
                with stream, dest.open("xb") as handle:
                    handle.write(stream.read())


def read_rss(pid):
    try:
        text = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
    except OSError:
        return 0
    return max([0] + [int(line.split()[1]) * 1024 for line in text.splitlines() if line.startswith(("VmRSS:", "VmHWM:"))])


def monitor(command, env, wall_limit=900.0, rss_limit=None):
    if rss_limit is not None:
        require(Path("/proc/self/status").is_file(), "linux_rss_watchdog_required")
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=os.name == "posix")
    started, peak = time.monotonic(), 0
    try:
        while proc.poll() is None:
            require(time.monotonic() - started <= wall_limit, "worker_wall_time_limit")
            if rss_limit is not None:
                peak = max(peak, read_rss(proc.pid))
                require(peak <= rss_limit, "worker_rss_limit")
            time.sleep(0.02)
        return proc.returncode, peak
    finally:
        if proc.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
        proc.wait()


def execute_isolated(repo, mode, report):
    git(repo, "cat-file", "-e", SHA + "^{commit}")
    before = git(repo, "status", "--porcelain", "--untracked-files=all")
    archive = git(repo, "archive", "--format=tar", SHA, *ARCHIVE_PATHS)
    sites = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
    with tempfile.TemporaryDirectory(prefix="research370-") as directory:
        temp = Path(directory)
        root = temp / "source"
        root.mkdir()
        extract_tree(archive, root)
        verify_source_tree(root)
        worker_path = temp / "measure_search_issue370.py"
        worker_path.write_bytes(Path(__file__).read_bytes())
        output = temp / "worker.json"
        report["stage"] = "worker"
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                   PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
        rc, peak = monitor([sys.executable, "-I", "-S", str(worker_path), "--_worker", str(root), str(output), json.dumps(sites), mode],
                           env, rss_limit=10 * 1024 ** 3 if mode == "model" else None)
        report.update(worker_rc=rc, observed_peak_rss_bytes=peak if mode == "model" else None)
        require(output.is_file(), "worker_result_missing")
        worker_report = json.loads(output.read_bytes())
        require(worker_report.get("END") == "search_quality_issue370_worker/2" and worker_report.get("source_sha") == SHA, "invalid_worker_result")
        report["worker"] = worker_report
        report["model_loaded"] = worker_report.get("model_loaded", False)
        require(rc == worker_report.get("rc") == 0, "worker_execution_failed")
    after = git(repo, "status", "--porcelain", "--untracked-files=all")
    require(before == after, "worktree_status_changed")
    report.update(worktree_status_unchanged=True, stage="complete", rc=0,
                  status=worker_report["status"], quality_measured=worker_report["quality_measured"])


def main(argv=None):
    print("measure_search_issue370.py v1", flush=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--smoke-only", action="store_true")
    modes.add_argument("--baseline-only", action="store_true")
    modes.add_argument("--run-models", action="store_true")
    parser.add_argument("--protocol", type=int)
    args = parser.parse_args(argv)
    mode = "model" if args.run_models else "baseline" if args.baseline_only else "smoke"
    if args.out.exists() or args.out.is_symlink():
        print("STOP: output_exists RC=2")
        return 2
    if not args.out.parent.is_dir():
        print("STOP: output_directory_missing RC=2")
        return 2
    if mode == "model" and args.protocol != PROTOCOL_COMMENT:
        print("STOP: model_run_requires_explicit_protocol_flag RC=2")
        return 2
    report = {"schema": "search_quality_issue370/1", "source_sha": SHA,
              "protocol_comment": PROTOCOL_COMMENT, "runner_version": "v1",
              "original_measure_script_sha256": ORIGINAL_SCRIPT_SHA256,
              "mode": mode, "rc": 1, "status": "execution_failed", "stage": "preflight",
              "model_loaded": False, "quality_measured": False, "network_used": False}
    started = time.perf_counter()
    try:
        execute_isolated(args.repo.resolve(), mode, report)
    except Exception as exc:  # noqa: BLE001 -- fixed error codes or exception type only
        report["error"] = str(exc) if isinstance(exc, EvalError) else type(exc).__name__
    report.update(duration_seconds=time.perf_counter() - started, END="search_quality_issue370/1")
    try:
        save_json(args.out, report)
    except Exception:  # noqa: BLE001 -- never overwrite a concurrent result
        print("STOP: result_save_failed RC=2")
        return 2
    raw = args.out.read_bytes()
    print(f"status={report['status']} RC={report['rc']} bytes={len(raw)} sha256={digest(raw)}")
    return report["rc"]


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--_worker":
        sys.exit(run_worker(Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5]))
    sys.exit(main())
