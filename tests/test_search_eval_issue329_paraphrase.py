"""Offline structure/identity checks for the standalone synthetic slice #369.

No ranking, model, weights, network, runtime search, or quality measurements.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from v8unpack_agent.modules import ModuleEntry, ModuleIndex
from v8unpack_agent.search_contract import (
    ArtifactRef,
    OwnerLink,
    OwnerRef,
    SearchDocument,
    SearchDocumentSet,
    SearchQuery,
    identity_key,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs/research/search_eval_issue329_paraphrase.json"
GROUPS = ('configuration:ordinary_application', 'configuration:managed_application', 'configuration:external_connection', 'configuration:session', 'metadata_object:object', 'external_data_processor:object', 'external_report:object', 'metadata_object:manager', 'metadata_object:record_set', 'common_command:command', 'metadata_object_command:command', 'metadata_object:value_manager', 'metadata_object:HTTPService:service', 'metadata_object:WebService:service', 'common_module:common_module', 'form:metadata_object_form', 'form:common_form', 'form:external_data_processor_form', 'form:external_report_form')
OLD_HASHES = {
    "search_eval_issue321.json": "35166f3e74ca7047731fc05a1cf3ed564530a02cf65cf03f2371d3ff48efa052",
    "search_eval_issue321_hard.json": "f94c3d222eb34d2c9b629a3c51379fc7661f42dd252388b1b596da5026b70ec3",
}


def stems(text):
    split = re.sub(r"(?<=[a-zа-я])(?=[A-ZА-Я])", " ", text)
    return {word[:5] for word in re.findall(r"\w+", split.casefold()) if len(word) > 3}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def load_dataset():
    raw = DATA_PATH.read_bytes()
    assert raw.endswith(b"\n") and not raw.startswith(b"\xef\xbb\xbf")
    return json.loads(raw)


def actual_group(artifact):
    if artifact["form_key"] is not None:
        suffix = {
            "metadata_object_form": "metadata_object_form",
            "common_form": "common_form",
            "external_data_processor": "external_data_processor_form",
            "external_report": "external_report_form",
        }[artifact["owner_kind"]]
        return "form:" + suffix
    if artifact["module_kind"] == "service":
        return "metadata_object:" + artifact["metadata_type"] + ":service"
    return artifact["owner_kind"] + ":" + artifact["module_kind"]


def construct_corpus(data):
    refs = {}
    owners = {}
    docs = []
    links = []
    entries = []
    fragments = {}
    sources = set()
    for artifact in data["artifacts"]:
        owner = OwnerRef(
            owner_kind=artifact["owner_kind"],
            metadata_type=artifact["metadata_type"],
            owner_name=artifact["owner_name"],
        )
        key = artifact["form_key"]
        if key is None:
            text = "\n".join(p["text"] for p in artifact["procedures"])
            raw = text.encode("utf-8")
            entry = ModuleEntry(
                module_kind=artifact["module_kind"],
                owner_kind=artifact["owner_kind"],
                metadata_type=artifact["metadata_type"],
                owner_name=artifact["owner_name"],
                relative_path=artifact["source_path"], read_status="ok",
                size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            )
            assert ModuleEntry.from_dict(entry.to_dict()) == entry
            entries.append(entry)
            ref = ArtifactRef.from_module_entry(entry)
        else:
            assert isinstance(key, list) and len(key) == 4
            assert artifact["module_kind"] == "form"
            assert artifact["form_name"] == key[3]
            installed_owner = owner if artifact["link_basis"] == "form_key" else None
            ref = ArtifactRef.form(*key, owner=installed_owner, source_path=artifact["source_path"])
        assert ref.artifact_id == artifact["id"]
        assert ArtifactRef.from_dict(ref.to_dict()) == ref
        assert actual_group(artifact) == artifact["group"]
        assert artifact["group"] in GROUPS
        source = identity_key(artifact["source_path"])
        assert source not in sources
        sources.add(source)
        aid = identity_key(ref.artifact_id)
        assert aid not in refs
        refs[aid] = ref
        owners[aid] = owner
        basis = artifact["link_basis"]
        expected = "module_entry" if key is None else (
            "form_key" if artifact["owner_kind"] == "metadata_object_form" else "unconfirmed"
        )
        assert basis == expected
        link = OwnerLink(owner=owner, artifact=ref, basis=basis)
        assert link.confirmed is (basis != "unconfirmed")
        assert OwnerLink.from_dict(link.to_dict()) == link
        if basis == "unconfirmed":
            assert ref.owner is None
        links.append(link)
        for procedure in artifact["procedures"]:
            document = SearchDocument(artifact=ref, text=procedure["text"], procedure=procedure["name"])
            assert SearchDocument.from_dict(document.to_dict(include_text=True)) == document
            did = identity_key(document.document_id)
            assert did not in fragments
            fragments[did] = (artifact, procedure)
            docs.append(document)
    ModuleIndex.from_entries(entries)
    corpus = SearchDocumentSet.from_documents(docs)
    assert SearchDocumentSet.from_dict(corpus.to_dict(include_text=True)) == corpus
    return refs, owners, fragments, links


def exact_candidates(text, refs):
    key = identity_key(text)
    by_id = {ref.artifact_id for ref in refs.values() if ref.identity == key}
    if by_id:
        return by_id
    return {
        ref.artifact_id for ref in refs.values()
        if (ref.form_key is not None and identity_key(ref.form_key[3]) == key)
        or (ref.owner is not None and ref.owner.owner_name is not None
            and identity_key(ref.owner.owner_name) == key)
    }


def validate_dataset(data):
    assert data["schema"] == "search_eval/1" and data["issue"] == 369
    assert data["synthetic"] is True
    assert isinstance(data["artifacts"], list) and isinstance(data["queries"], list)
    assert len(data["artifacts"]) >= 34
    protocol = data["protocol"]
    assert protocol["groups"] == list(GROUPS)
    assert protocol["minimum_free_per_group"] == 8
    assert protocol["minimum_intents_per_group"] == 4
    assert protocol["minimum_lexical_disjoint_per_group"] == 2
    assert protocol["whole_doc_max_chars"] == 2000
    assert protocol["thresholds_free"] == {"recall@5": 0.80, "mrr@10": 0.60}
    assert protocol["top_k"] == {"recall": 5, "mrr": 10, "ndcg": 10}
    assert protocol["search_before_merge"] == "forbidden"
    assert protocol["corpus_mode"] == "standalone_synthetic"
    assert protocol["gold_basis"] == "content_only_before_any_ranking"
    required_artifact = {
        "id", "owner_kind", "metadata_type", "owner_name", "module_kind",
        "form_name", "synonym", "large", "procedures", "form_key", "source_path",
        "link_basis", "group",
    }
    for artifact in data["artifacts"]:
        assert required_artifact <= artifact.keys()
        assert isinstance(artifact["id"], str) and artifact["id"]
        assert type(artifact["large"]) is bool
        assert isinstance(artifact["procedures"], list) and artifact["procedures"]
        for procedure in artifact["procedures"]:
            assert isinstance(procedure["name"], str) and procedure["name"].isidentifier()
            assert isinstance(procedure["text"], str) and procedure["text"].strip()
    refs, _, fragments, _ = construct_corpus(data)
    seen_ids = set()
    seen_texts = set()
    counts = Counter()
    groups = defaultdict(list)
    intents = defaultdict(list)
    gold_artifacts = defaultdict(set)
    required_query = {"id", "class", "text", "gold", "gold_fragments", "expect_status", "group", "tag", "gold_rationale"}
    for query in data["queries"]:
        assert required_query <= query.keys()
        assert isinstance(query["id"], str) and query["id"]
        assert isinstance(query["text"], str) and query["text"].strip()
        for name, seen in (("id", seen_ids), ("text", seen_texts)):
            normalized = identity_key(query[name])
            assert normalized not in seen
            seen.add(normalized)
        SearchQuery(text=query["text"])
        assert isinstance(query["gold"], list) and isinstance(query["gold_fragments"], list)
        for name in ("gold", "gold_fragments"):
            assert all(isinstance(value, str) for value in query[name])
            assert len({identity_key(value) for value in query[name]}) == len(query[name])
        assert isinstance(query["gold_rationale"], str) and query["gold_rationale"].strip()
        gold = {identity_key(value) for value in query["gold"]}
        fragment_gold = {identity_key(value) for value in query["gold_fragments"]}
        assert gold <= refs.keys()
        assert fragment_gold <= fragments.keys()
        assert all(value.split("#", 1)[0] in gold for value in fragment_gold)
        kind = query["class"]
        assert kind in {"free", "ambiguous", "negative"}
        counts[kind] += 1
        if kind == "free":
            assert gold and fragment_gold and query["expect_status"] == "found"
            assert query["tag"] == "paraphrase"
            assert query["group"] in GROUPS
            assert isinstance(query["intent_id"], str) and query["intent_id"]
            assert type(query["lexical_disjoint"]) is bool
            assert type(query["tail"]) is bool
            groups[query["group"]].append(query)
            intents[query["intent_id"]].append(query)
            gold_artifacts[query["group"]].update(gold)
            target_stems = set()
            for fid in fragment_gold:
                artifact, procedure = fragments[fid]
                assert artifact["group"] == query["group"]
                assert identity_key(procedure["name"]) not in identity_key(query["text"])
                target_stems.update(stems(procedure["name"] + " " + procedure["text"]))
                for field in ("owner_name", "form_name", "id"):
                    value = artifact[field]
                    if value:
                        assert identity_key(value) not in identity_key(query["text"])
                if query["tail"]:
                    before = 0
                    for candidate in artifact["procedures"]:
                        if candidate["name"] == procedure["name"]:
                            break
                        before += len(candidate["text"]) + 1
                    assert before > protocol["whole_doc_max_chars"]
                    assert artifact["large"] is True
            assert query["lexical_disjoint"] is (not bool(stems(query["text"]) & target_stems))
            distractor = query["distractor"]
            if distractor is not None:
                assert isinstance(distractor, str)
                assert identity_key(distractor) in refs and identity_key(distractor) not in gold
                assert isinstance(query["distractor_reason"], str) and query["distractor_reason"].strip()
            else:
                assert query["distractor_reason"] is None
        elif kind == "ambiguous":
            assert query["group"] == "control:ambiguous"
            assert len(gold) >= 2 and not fragment_gold and query["expect_status"] == "ambiguous"
            assert set(query["gold"]) == exact_candidates(query["text"], refs)
        else:
            assert query["group"] == "control:negative"
            assert not gold and not fragment_gold and query["expect_status"] == "none"
            assert not exact_candidates(query["text"], refs)
    assert counts["free"] >= 152 and counts["ambiguous"] >= 8 and counts["negative"] >= 8
    assert set(groups) == set(GROUPS)
    assert sum(q.get("tail", False) for q in data["queries"]) >= 8
    assert sum(q.get("distractor") is not None for q in data["queries"]) >= 8
    for group in GROUPS:
        rows = groups[group]
        assert len(rows) >= 8 and len({q["intent_id"] for q in rows}) >= 4
        assert sum(q["lexical_disjoint"] for q in rows) >= 2
        minimum_artifacts = 1 if group.startswith("configuration:") else 2
        assert len(gold_artifacts[group]) >= minimum_artifacts
        for aid in gold_artifacts[group]:
            selected = [q for q in rows if aid in {identity_key(g) for g in q["gold"]}]
            assert len(selected) >= 4
            minimum_intents = 4 if group.startswith("configuration:") else 2
            assert len({q["intent_id"] for q in selected}) >= minimum_intents
    for rows in intents.values():
        assert len(rows) >= 2
        assert len({q["group"] for q in rows}) == 1
        assert len({tuple(q["gold_fragments"]) for q in rows}) == 1
    diversity = {
        "metadata_object:object": {"Document", "Catalog"},
        "metadata_object:record_set": {"AccumulationRegister", "InformationRegister"},
    }
    for group, required in diversity.items():
        assert required <= {a["metadata_type"] for a in data["artifacts"] if a["group"] == group}
    for group in ("metadata_object:manager", "metadata_object_command:command", "form:metadata_object_form"):
        assert len({a["metadata_type"] for a in data["artifacts"] if a["group"] == group}) >= 2
    for text in strings(data):
        assert chr(92) not in text
        assert not any(marker in text for marker in ("/home/", "/Users/", "localhost", "postgres://", "password=", "token="))
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        assert not re.search(r"(?i)[a-z]:[/\\]", text)
    return {"artifacts": len(data["artifacts"]), "queries": len(data["queries"]), "groups": len(groups), "intents": len(intents)}


def test_candidate_format_identities_and_coverage():
    validate_dataset(load_dataset())


def test_old_issue321_sets_unchanged():
    for name, expected in OLD_HASHES.items():
        assert hashlib.sha256((DATA_PATH.parent / name).read_bytes()).hexdigest() == expected


def test_validator_rejects_corrupt_candidates():
    import copy

    import pytest

    original = load_dataset()
    bad = copy.deepcopy(original)
    bad["queries"][0]["gold"] = ["missing:artifact"]
    with pytest.raises(AssertionError):
        validate_dataset(bad)
    bad = copy.deepcopy(original)
    bad["queries"][0]["lexical_disjoint"] = not bad["queries"][0]["lexical_disjoint"]
    with pytest.raises(AssertionError):
        validate_dataset(bad)
    bad = copy.deepcopy(original)
    bad["queries"][1]["id"] = bad["queries"][0]["id"]
    with pytest.raises(AssertionError):
        validate_dataset(bad)
    bad = copy.deepcopy(original)
    bad["queries"] = bad["queries"][8:]
    with pytest.raises(AssertionError):
        validate_dataset(bad)
