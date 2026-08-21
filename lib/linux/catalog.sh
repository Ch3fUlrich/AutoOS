#!/usr/bin/env bash
# AutoOS catalog loading, filtering and dependency resolution for Linux.
#
# JSON is parsed with python3 rather than jq: python3 ships with Debian, Ubuntu
# and Raspberry Pi OS, whereas jq must be installed first — and this code has to
# run before anything has been installed. The python here is a pure transform;
# all decisions stay in shell.
#
# shellcheck shell=bash

CATALOG_PATH=""
declare -a CAT_ID CAT_NAME CAT_DESC CAT_PROVIDER CAT_PACKAGE CAT_REQUIRES
declare -a CAT_PROFILES CAT_POST CAT_PROMPT CAT_NOTES CAT_GROUP CAT_VERIFY CAT_CASK

catalog_require_python() {
    if ! has_cmd python3; then
        ui_err "python3 is required to read the component catalog but was not found."
        ui_muted "    Install it first:  ${AUTOOS_SUDO} apt-get install -y python3"
        return 1
    fi
}

# catalog_validate <path> — prints one problem per line, exit 1 if any.
catalog_validate() {
    catalog_require_python || return 1
    python3 - "$1" <<'PY'
import json, sys, re
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        cat = json.load(fh)
except Exception as exc:
    print(f"catalog: not valid JSON ({exc})"); sys.exit(1)

VALID = {"winget","choco","npm","apt","snap","brew","script","custom"}
problems, seen = [], set()
cats = cat.get("categories")
if not cats:
    print("catalog: missing 'categories'"); sys.exit(1)

all_ids = {c.get("id") for grp in cats for c in grp.get("components", [])}
for grp in cats:
    if not grp.get("id"):   problems.append("category: missing 'id'")
    if not grp.get("name"): problems.append(f"category '{grp.get('id')}': missing 'name'")
    for c in grp.get("components", []):
        cid = c.get("id")
        where = f"component '{cid}'"
        if not cid:
            problems.append(f"{grp.get('id')}: a component has no 'id'"); continue
        if cid in seen: problems.append(f"{where}: duplicate id")
        seen.add(cid)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cid):
            problems.append(f"{where}: id must be lower-case kebab-case")
        if not c.get("name"):        problems.append(f"{where}: missing 'name'")
        desc = c.get("description")
        if not desc:                 problems.append(f"{where}: missing 'description'")
        elif len(desc) > 70:         problems.append(f"{where}: description longer than 70 chars")
        prov = c.get("provider")
        if not prov:                 problems.append(f"{where}: missing 'provider'")
        elif prov not in VALID:      problems.append(f"{where}: unknown provider '{prov}'")
        if not c.get("package"):     problems.append(f"{where}: missing 'package'")
        for r in c.get("requires", []):
            if r not in all_ids:     problems.append(f"{where}: requires unknown component '{r}'")
            if r == cid:             problems.append(f"{where}: requires itself")
        v = c.get("verify")
        if "verify" in c and not (v or "").strip():
            problems.append(f"{where}: 'verify' is present but empty")
        p = c.get("prompt")
        if p and p not in (cat.get("prompts") or {}):
            problems.append(f"{where}: references undefined prompt '{p}'")

for p in problems: print(p)
sys.exit(1 if problems else 0)
PY
}

# catalog_load <path> <arch> <is_headless>
# Fills the CAT_* arrays with components that can run on this machine.
catalog_load() {
    local path="$1" arch="$2" headless="$3" line
    CATALOG_PATH="$path"
    catalog_require_python || return 1

    CAT_ID=(); CAT_NAME=(); CAT_DESC=(); CAT_PROVIDER=(); CAT_PACKAGE=()
    CAT_REQUIRES=(); CAT_PROFILES=(); CAT_POST=(); CAT_PROMPT=(); CAT_NOTES=(); CAT_GROUP=()
    CAT_VERIFY=(); CAT_CASK=()

    # Delimiter is US (0x1f), NOT tab: tab is an IFS *whitespace* character, so
    # bash collapses runs of them and every empty field shifts the columns left.
    while IFS=$'\x1f' read -r id name desc provider package requires profiles post prompt notes group verify cask; do
        [[ -z "$id" ]] && continue
        CAT_ID+=("$id");           CAT_NAME+=("$name");     CAT_DESC+=("$desc")
        CAT_PROVIDER+=("$provider");CAT_PACKAGE+=("$package");CAT_REQUIRES+=("$requires")
        CAT_PROFILES+=("$profiles");CAT_POST+=("$post");     CAT_PROMPT+=("$prompt")
        CAT_NOTES+=("$notes");      CAT_GROUP+=("$group");   CAT_VERIFY+=("$verify")
        CAT_CASK+=("$cask")
    done < <(python3 - "$path" "$arch" "$headless" <<'PY'
import json, sys
path, arch, headless = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
cat = json.load(open(path, encoding="utf-8"))
for grp in cat.get("categories", []):
    if grp.get("requiresDisplay") and headless:
        continue                      # hide, never show-and-fail
    for c in grp.get("components", []):
        arches = c.get("arch")
        if arches and arch not in arches:
            continue
        print("\x1f".join([
            c.get("id",""), c.get("name",""), c.get("description",""),
            c.get("provider",""), c.get("package",""),
            ",".join(c.get("requires", [])), ",".join(c.get("profiles", [])),
            c.get("postInstall",""), c.get("prompt",""),
            (c.get("notes","") or "").replace("\x1f"," "), grp.get("name",""),
            c.get("verify","") or "",
            "1" if c.get("cask") else "0",
        ]))
PY
    )
}

catalog_index_of() {
    local want="$1" i
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        [[ "${CAT_ID[i]}" == "$want" ]] && { echo "$i"; return 0; }
    done
    return 1
}

# catalog_profile_defaults <profile> — echoes ids pre-selected for that profile.
catalog_profile_defaults() {
    local profile="$1" i out=""
    [[ "$profile" == "custom" ]] && { echo ""; return; }
    for ((i = 0; i < ${#CAT_ID[@]}; i++)); do
        [[ ",${CAT_PROFILES[i]}," == *",${profile},"* ]] && out+="${CAT_ID[i]} "
    done
    echo "${out% }"
}

# catalog_resolve <id...> — echoes a dependency-complete, topologically sorted
# id list. Dependencies always precede the components that need them.
PLAN_IDS=""
PLAN_AUTO=""
catalog_resolve() {
    local requested=("$@")
    local -a wanted=() queue=("$@")
    local id dep i

    while ((${#queue[@]})); do
        id="${queue[0]}"; queue=("${queue[@]:1}")
        [[ " ${wanted[*]} " == *" $id "* ]] && continue
        i="$(catalog_index_of "$id")" || continue
        wanted+=("$id")
        if [[ -n "${CAT_REQUIRES[i]}" ]]; then
            IFS=',' read -ra deps <<<"${CAT_REQUIRES[i]}"
            for dep in "${deps[@]}"; do
                [[ -n "$dep" ]] && queue+=("$dep")
            done
        fi
    done

    local -a done_list=() visiting=()
    local ordered=""

    _visit() {
        local node="$1" idx d
        [[ " ${done_list[*]} " == *" $node "* ]] && return 0
        if [[ " ${visiting[*]} " == *" $node "* ]]; then
            ui_err "Dependency cycle in catalog at '$node'"; return 1
        fi
        visiting+=("$node")
        idx="$(catalog_index_of "$node")" || return 0
        if [[ -n "${CAT_REQUIRES[idx]}" ]]; then
            IFS=',' read -ra ds <<<"${CAT_REQUIRES[idx]}"
            for d in "${ds[@]}"; do
                [[ -z "$d" ]] && continue
                [[ " ${wanted[*]} " == *" $d "* ]] && { _visit "$d" || return 1; }
            done
        fi
        visiting=("${visiting[@]/$node}")
        done_list+=("$node")
        ordered+="$node "
        return 0
    }

    for id in "${wanted[@]}"; do _visit "$id" || return 1; done

    PLAN_IDS="${ordered% }"
    PLAN_AUTO=""
    for id in $PLAN_IDS; do
        [[ " ${requested[*]} " == *" $id "* ]] || PLAN_AUTO+="$id "
    done
    PLAN_AUTO="${PLAN_AUTO% }"
    echo "$PLAN_IDS"
}

# catalog_prompt_field <path> <prompt-key> <field>
catalog_prompt_field() {
    python3 - "$1" "$2" "$3" <<'PY'
import json, sys
cat = json.load(open(sys.argv[1], encoding="utf-8"))
print((cat.get("prompts", {}).get(sys.argv[2], {}) or {}).get(sys.argv[3], "") or "")
PY
}

# catalog_profile_list <path> — echoes "name<TAB>description" per profile.
catalog_profile_list() {
    python3 - "$1" <<'PY'
import json, sys
cat = json.load(open(sys.argv[1], encoding="utf-8"))
for name, desc in (cat.get("profiles") or {}).items():
    print(f"{name}\t{desc}")
PY
}
