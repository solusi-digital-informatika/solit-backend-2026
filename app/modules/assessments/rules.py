from app.modules.assessments.schemas import ProviderItem

CATEGORIES = ("palette", "lighting", "materials", "mood")
RECOMMENDATIONS = ("REUSE_CANDIDATE", "ADAPT_CANDIDATE", "REVIEW_REQUIRED", "RECREATE_CANDIDATE")
CATEGORY_MAP = {"PALETTE": "palette", "LIGHTING": "lighting", "COMPOSITION": "composition", "MATERIAL": "materials",
    "MOOD": "mood", "TYPOGRAPHY": "typography", "SUBJECT": "subject"}

def normalized(value):
    return " ".join(value.lower().split())

def direction_tokens(direction):
    return {"palette": [normalized(x["name"]) for x in direction["palette"]],
        "lighting": [normalized(x) for key, x in direction["lighting"].items() if x and key != "notes"],
        "materials": [normalized(x) for x in direction["materials"]], "mood": [normalized(x) for x in direction["mood"]]}

def assess(snapshot, mode):
    target = snapshot["newDirection"]
    expected = direction_tokens(target)
    output = []
    for version in snapshot["versions"]:
        recorded = version["recordedAttributes"]
        supporting, conflicting, missing = [], [], []
        def evidence(rule, text, source="RULE", source_id=None):
            return {"source": source, "statement": text, "sourceId": source_id, "ruleId": rule}
        empty = [category for category in CATEGORIES if not recorded.get(category)]
        if len(empty) >= 2:
            missing = ["Missing recorded " + c + " attributes." for c in empty]
        matches = 0
        conflicts = 0
        for category in CATEGORIES:
            actual = {normalized(x) for x in recorded.get(category, [])}
            desired = set(expected[category])
            if actual and desired:
                if actual & desired:
                    matches += 1
                    supporting.append(evidence("R-STYLE-OVERLAP", "Recorded " + category + " overlaps the new direction.", "ASSET_METADATA", version["id"]))
                else:
                    conflicts += 1
                    conflicting.append(evidence("R-STYLE-OVERLAP", "Recorded " + category + " has no matching token in the new direction.", "ASSET_METADATA", version["id"]))
        hard_conflict = False
        hard_missing = False
        for context, source, source_id in [(target, "DIRECTION", snapshot["newDirectionRevisionId"]),
                                           (snapshot["brief"] or {}, "BRIEF", snapshot["briefRevisionId"])]:
            for forbidden in context.get("forbiddenAttributes", []):
                category = CATEGORY_MAP.get(forbidden["category"])
                if category and normalized(forbidden["value"]) in {normalized(x) for x in recorded.get(category, [])}:
                    hard_conflict |= forbidden["hard"]
                    conflicting.append(evidence("R-FORBIDDEN-CONFLICT", "Recorded attribute conflicts with exclusion: " + forbidden["label"] + ".", source, source_id))
            for required in context.get("requiredAttributes", context.get("requirements", [])):
                category = CATEGORY_MAP.get(required["category"])
                if required["hard"] and category and normalized(required["value"]) not in {normalized(x) for x in recorded.get(category, [])}:
                    hard_missing = True
                    conflicting.append(evidence("R-REQUIRED-MISSING", "A hard requirement has no recorded match: " + required["label"] + ".", source, source_id))
        if missing:
            recommendation, uncertainty = "REVIEW_REQUIRED", "HIGH"
            rationale = "Key recorded attributes are missing; compatibility cannot be determined."
            supporting.append(evidence("R-MISSING-METADATA", "Review is needed because the recorded metadata is incomplete.", "ASSET_METADATA", version["id"]))
        elif hard_conflict or conflicts >= 3:
            recommendation, uncertainty = "RECREATE_CANDIDATE", "LOW"
            rationale = "Recorded attributes conflict with several style categories or a hard exclusion."
        elif hard_missing:
            recommendation, uncertainty = "REVIEW_REQUIRED", "HIGH"
            rationale = "A hard requirement is not evidenced in the recorded attributes."
            missing.append("Confirm the hard requirement before deciding.")
        elif conflicts == 0 and matches >= 2 and not conflicting:
            recommendation, uncertainty = "REUSE_CANDIDATE", "MEDIUM"
            rationale = "Recorded style attributes overlap the new direction without identified conflicts."
        elif (1 <= conflicts <= 2 or conflicting) and recorded.get("composition"):
            recommendation, uncertainty = "ADAPT_CANDIDATE", "MEDIUM"
            rationale = "Recorded composition may be retained while conflicting style attributes are revised."
        else:
            recommendation, uncertainty = "REVIEW_REQUIRED", "HIGH"
            rationale = "The available metadata does not establish enough compatibility or reusable composition."
            missing.append("Clarify reusable composition and intended purpose.")
        if not supporting and not conflicting:
            supporting.append(evidence("R-MISSING-METADATA", "The available recorded metadata is insufficient.", "ASSET_METADATA", version["id"]))
        reason = "Resolve missing evidence first." if missing else "Review conflicting attributes." if conflicting else "Check compatible recorded attributes."
        if version["pinned"]:
            reason += " This version is pinned in an approved collection."
        actions = {"REVIEW_REQUIRED": ["Complete the missing metadata and review the asset."],
            "RECREATE_CANDIDATE": ["Create a new candidate version while retaining the original."],
            "ADAPT_CANDIDATE": ["Create a revised version preserving useful composition."],
            "REUSE_CANDIDATE": ["Confirm reuse for the new direction."]}[recommendation]
        item = {"assetVersionId": version["id"], "recommendation": recommendation, "rationale": rationale,
            "supportingEvidence": supporting, "conflictingEvidence": conflicting, "missingInformation": missing,
            "suggestedActions": actions, "uncertaintyLevel": uncertainty,
            "requiresHumanReview": recommendation != "REUSE_CANDIDATE", "analysisSource": "RULES" if mode == "RULES_ONLY" else "HYBRID" if mode == "HYBRID" else "AI",
            "priorityReason": reason}
        output.append(ProviderItem.model_validate(item).model_dump(mode="json"))
    order = {"REVIEW_REQUIRED": 0, "RECREATE_CANDIDATE": 1, "ADAPT_CANDIDATE": 2, "REUSE_CANDIDATE": 3}
    by_id = {v["id"]: v for v in snapshot["versions"]}
    output.sort(key=lambda i: (order[i["recommendation"]], not by_id[i["assetVersionId"]]["pinned"], by_id[i["assetVersionId"]]["title"], i["assetVersionId"]))
    return output
