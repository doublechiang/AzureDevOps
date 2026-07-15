import os
import requests
from flask import Flask, request
import sys
import re
import time
import urllib.parse
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger=logging.getLogger(__name__)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

GUID_CACHE = {}

app = Flask(__name__)

# 從環境變數讀取 PAT (安全性考量)
PAT = os.environ.get("AZURE_PAT")
ORG_NAME = "quanta01" 

Area_Manager = {
    r"QCIDiag\QCT" : "EasonLin@quantatw.com",
    r"QCIDiag\Amazon" : "Joe_Huang@quantatw.com",
    r"QCIDiag\Google" : "Alex.Lee@quantatw.com",
    r"QCIDiag\Oracle" : "Alex.Lee@quantatw.com",
    r"QCIDiag\Meta" : "Lance.Wu@quantatw.com",
    r"QCIDiag\Msft" : "Wei-Kai.Huang@quantatw.com",
    r"QCIDiag\Diaglib" : "chun-yu.chiang@quantatw.com",
    r"TE_Test" : "chun-yu.chiang@quantatw.com",
}

My_Email = "chun-yu.chiang@quantatw.com"

# The MFG-notification rule only applies to releases for these Diag customer areas
# (per the close-criteria spec). Diaglib / TE_Test etc. are internal and exempt.
RELEASE_CUSTOMER_AREAS = (
    r"QCIDiag\QCT",
    r"QCIDiag\Oracle",
    r"QCIDiag\Meta",
    r"QCIDiag\Msft",
    r"QCIDiag\Amazon",
    r"QCIDiag\Google",
)

# How fresh the MFG tag must be relative to the closure date.
MFG_TAG_WINDOW = timedelta(days=1)


def is_release_customer_area(area_path):
    return any(area_path == a or area_path.startswith(a + "\\") for a in RELEASE_CUSTOMER_AREAS)


def parse_ado_datetime(s):
    """Parse an ADO ISO-8601 UTC timestamp (e.g. '2026-06-21T22:52:58.14Z') to a
    naive UTC datetime. Fractional seconds and the trailing 'Z' are dropped; all
    ADO timestamps are UTC, so comparisons stay consistent."""
    if not s:
        return None
    try:
        base = s.split('.')[0].rstrip('Z').split('+')[0]
        return datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, AttributeError):
        return None


# MFG (factory-side) TE groups. When a Feature (= a release) is closed, at least
# one member of these groups must be @mentioned somewhere on the work item, so
# engineers build the habit of notifying the factory instead of only tagging our
# own department (QCI TE, deliberately NOT listed here). Group descriptors are
# stable; look up a new one via:
#   GET https://vssps.dev.azure.com/{org}/_apis/graph/groups (paginate + match displayName)
MFG_TE_GROUPS = {
    "QCG TE": "vssgp.Uy0xLTktMTU1MTM3NDI0NS00MjQ1MTY2MzU3LTc3Njk0Mjg1LTIzNTE2ODMyODAtNDU4ODgyNjI5LTEtMTAyMzgzOTA3Ni03OTE2MjcwODYtMjUxMDA0NzUyOC0yNjIzMDE2NjM2",
    "QMF TE": "vssgp.Uy0xLTktMTU1MTM3NDI0NS00MjQ1MTY2MzU3LTc3Njk0Mjg1LTIzNTE2ODMyODAtNDU4ODgyNjI5LTEtMjA4MzAxMzE3MC00MjE3Njk1ODEwLTI5MjY5NzAyNzMtMzI4Mjc5MTUxMg",
    "QMN TE": "vssgp.Uy0xLTktMTU1MTM3NDI0NS00MjQ1MTY2MzU3LTc3Njk0Mjg1LTIzNTE2ODMyODAtNDU4ODgyNjI5LTEtMTIyNzk0NzU1NC0xNjA1MzM5MjE1LTI0NTc3ODA4OTQtMzQ0NTI5OTAyMQ",
    "QTMC TE": "vssgp.Uy0xLTktMTU1MTM3NDI0NS00MjQ1MTY2MzU3LTc3Njk0Mjg1LTIzNTE2ODMyODAtNDU4ODgyNjI5LTEtMTY5NTk5OTcwOC0xMDQwNDc0Njk5LTI3MTQ4Nzc1MzMtMjQ2MTQ3MzA",
}

MENTION_RE = re.compile(r'data-vss-mention="version:2\.0,([0-9a-fA-F-]+)"')

# A formal release is submitted to Quanta VRC (Version Release Control), which
# yields a 6-digit VRCID pointing to the released resource. Engineers record it
# free-text on the work item (e.g. "VRCID#335928"). Match is intentionally STRICT:
# the single canonical token "VRCID" (case-insensitive) + a 6-digit id, so the team
# standardises on one spelling. Anything else ("VRDID", "VRC ID", ...) will NOT
# match, reads as "no release", and the close is left alone; the engineer is
# expected to write it correctly. Presence of a VRCID = a real release, and only
# then must MFG be notified.
VRCID_RE = re.compile(r'VRCID\s*[#:]?\s*(\d{6})(?!\d)', re.IGNORECASE)

# Cache the resolved MFG member GUID set per instance; refresh daily.
_MFG_CACHE = {"guids": set(), "ts": 0.0}
_MFG_CACHE_TTL = 24 * 3600


def get_mfg_te_member_guids(auth):
    """Return the set of identity GUIDs (lowercased) of all MFG TE members.

    The GUIDs match the id used in `data-vss-mention="version:2.0,<GUID>"`.
    """
    now = time.time()
    if _MFG_CACHE["guids"] and (now - _MFG_CACHE["ts"]) < _MFG_CACHE_TTL:
        return _MFG_CACHE["guids"]

    descriptors = []
    for name, group_desc in MFG_TE_GROUPS.items():
        url = (f"https://vssps.dev.azure.com/{ORG_NAME}/_apis/graph/memberships/"
               f"{group_desc}?direction=down&api-version=7.1-preview.1")
        resp = requests.get(url, auth=auth)
        if resp.status_code != 200:
            logger.error(f"Failed to list members of MFG group {name}: {resp.status_code}")
            continue
        for m in resp.json().get("value", []):
            member = m.get("memberDescriptor", "")
            # aad./msa. = a person; vssgp. = a nested group (skip; groups can't be mentioned)
            if member.startswith(("aad.", "msa.")):
                descriptors.append(member)

    guids = set()
    for i in range(0, len(descriptors), 20):  # keep the query string short enough
        batch = urllib.parse.quote(",".join(descriptors[i:i + 20]))
        url = (f"https://vssps.dev.azure.com/{ORG_NAME}/_apis/identities"
               f"?subjectDescriptors={batch}&api-version=7.1")
        resp = requests.get(url, auth=auth)
        if resp.status_code == 200:
            for ident in resp.json().get("value", []):
                if ident.get("id"):
                    guids.add(ident["id"].lower())

    if guids:  # only cache a successful, non-empty resolution
        _MFG_CACHE["guids"] = guids
        _MFG_CACHE["ts"] = now
    return guids


def get_release_signals(work_item_id, wi_fields, auth):
    """Read the work item's revision history once and derive two things:

    - combined_text: Description + every discussion comment, used to detect a VRCID.
    - mention_events: list of (datetime, guids) recording WHEN each @mention was
      added, so we can check the "tagged within one day of closure" rule.
      Discussion (History) mentions are timed at the comment's revision; Description
      mentions are timed at the first revision they appear in (not on every re-save).
    """
    project = urllib.parse.quote(wi_fields.get("System.TeamProject", ""))
    url = (f"https://dev.azure.com/{ORG_NAME}/{project}/_apis/wit/workitems/"
           f"{work_item_id}/updates?api-version=7.0")
    resp = requests.get(url, auth=auth)
    texts = [wi_fields.get("System.Description", "") or ""]
    mention_events = []
    if resp.status_code != 200:
        logger.error(f"Failed to get updates for {work_item_id}: {resp.status_code}")
        combined = "\n".join(texts)
        return combined, mention_events

    updates = sorted(resp.json().get("value", []), key=lambda u: u.get("rev", 0))
    seen_desc = set()  # GUIDs already present in a prior Description revision
    for u in updates:
        f = u.get("fields", {}) or {}
        ts = parse_ado_datetime((f.get("System.ChangedDate", {}) or {}).get("newValue"))
        event_guids = set()

        hist = (f.get("System.History", {}) or {}).get("newValue")
        if hist:
            texts.append(hist)
            event_guids |= set(g.lower() for g in MENTION_RE.findall(hist))  # each comment = a fresh notification

        desc = (f.get("System.Description", {}) or {}).get("newValue")
        if desc:
            texts.append(desc)
            desc_guids = set(g.lower() for g in MENTION_RE.findall(desc))
            event_guids |= (desc_guids - seen_desc)  # only count newly-added description mentions
            seen_desc |= desc_guids

        if ts and event_guids:
            mention_events.append((ts, event_guids))

    combined = "\n".join(texts)
    return combined, mention_events


def mfg_tagged_within_window(mention_events, mfg_guids, close_time):
    """True if any MFG TE member was @mentioned within MFG_TAG_WINDOW of close_time."""
    if close_time is None:
        # Can't establish the closure time -> be lenient and accept any MFG mention.
        return any(guids & mfg_guids for _, guids in mention_events)
    return any(
        (guids & mfg_guids) and abs(ts - close_time) <= MFG_TAG_WINDOW
        for ts, guids in mention_events
    )


def get_identify_by_email(email, auth):
    """透過 Email 查詢 Azure DevOps 內部的 GUID"""
    if email in GUID_CACHE:
        return GUID_CACHE[email]
    try:
        url = f"https://vssps.dev.azure.com/{ORG_NAME}/_apis/identities?searchFilter=General&filterValue={email}&api-version=7.1"
        response = requests.get(url, auth=auth)
        if response.status_code == 200:
            data = response.json()
            if data['count'] > 0:
                ident = data['value'][0]
                guid = ident['id']
                display_name = ident.get('providerDisplayName') or ident.get('displayName') or email.split('@')[0]
                # print(f"DEBUG: Full Identity {ident}", flush=True)
                GUID_CACHE[email] = (guid, display_name)
                return guid, display_name
    except Exception as e:
        print(f"Error fetching GUID for {email}: {e}")
    return None, None
    

@app.route("/", methods=["POST"])
def check_issue_status():
    try:

        payload = request.json
        # print((f"Payload: {payload}"))
        if not payload or 'resource' not in payload:
            return "Invalid Payload", 400

        # get the work item state is changed from old value to new value, if we can get the newValue, then it is a state change
        resource = payload.get('resource', {})
        fields = resource.get('fields', {})

        # if it's feature or epic type, then ignore it
        work_item_type = fields.get('System.WorkItemType', {})
        logger.debug(f"Received workitem update for {resource['workItemId']} with Type {work_item_type}")
        if work_item_type in ['Epic']:
            return f"Ignore Item Type {work_item_type}", 200
            
        state_field = fields.get('System.State', {})
        new_state = state_field.get('newValue') if isinstance(state_field, dict) else None
        work_item_id = resource.get('workItemId') or resource.get('id')

        if new_state not in ['Closed', 'Done']:
            return f"Ignore Item State {new_state}", 200
                
        # 建立認證
        auth = ('', PAT)
        headers = {'Content-Type': 'application/json-patch+json'}

        # 1. 取得 Work Item 詳細資料 (包含 Relations)
        wi_url = f"https://dev.azure.com/{ORG_NAME}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=7.1"
        wi_response = requests.get(wi_url, auth=auth)
        # print(f"DEBUG: status code = {wi_response.status_code}", flush=True)
        if wi_response.status_code != 200:
            logger.error(f"Failed to get Work Item details {work_item_id}")
            return "Failed to get Work Item details", 200
        wi_full = wi_response.json()
        wi_fields = wi_full.get('fields', {})
        area_path = wi_fields.get('System.AreaPath', '')
        # request resource may not get complete type, update the type from wi_fields
        work_item_type = wi_fields.get('System.WorkItemType', '')

        # Check only specific area path
        if area_path not in Area_Manager:
            logger.debug(f"Ignore Item Area Path {area_path}")
            return "Ignore Item Area Path", 200

        tags_str = wi_fields.get('System.Tags', "")
        tags_list = [t.strip() for t in tags_str.split(';')] if tags_str else []
        if 'Meta' in area_path and 'FAVA' in tags_list:
            return "No need validate Meta FAVA close issues", 200

        # 這裡檢查是誰更改的，避免無窮迴圈 (如果是自動化帳號改的就跳過)
        assigned_to = wi_fields.get('System.AssignedTo', {})
        owner_email = assigned_to.get('uniqueName', '').lower()
        changed_by = wi_fields.get('System.ChangedBy')
        if isinstance(changed_by, str):
            if '<' in changed_by:
                changed_by = changed_by.split('<')[1].split('>')[0]
            else:
                changed_by = changed_by.lower()
        else: 
            changed_by = changed_by.get('uniqueName', '').lower().strip()
        
        relations = wi_full.get('relations', [])

        reasons = []

        # If there is a PR, check if it is completed
        pr_completed = False

        pr_links = [r['url'] for r in relations if 'PullRequestId' in r['url']]
        for pr_url in pr_links:
            parsed_url = urllib.parse.unquote(pr_url)
            pr_id = parsed_url.split('/')[-1]
            pr_api = f"https://dev.azure.com/{ORG_NAME}/_apis/git/pullrequests/{pr_id}?api-version=7.1"
            pr_data = requests.get(pr_api, auth=auth).json()
            pr_status = pr_data['status']
            if pr_status == 'active':
                # if the PR is active, then we can not close the issue
                reasons.append(f"Active PR found, You need to close PR {pr_id} then close issue.")  
                break
            if pr_status == 'completed':
                pr_completed = True

        # if the issue is not a Feature, then it must have a feature parent
        if work_item_type not in ['Feature']:
            if pr_completed:
                has_feature_parent = False
                for rel in relations:
                    if rel['attributes'].get('name') == 'Parent':
                        p_url = rel['url']
                        p_data = requests.get(p_url, auth=auth).json()
                        if p_data['fields'].get('System.WorkItemType') == 'Feature':
                            has_feature_parent = True
                            break
                if not has_feature_parent:
                    reasons.append("No Feature parent issue link")

        # Feature release close-criteria (per work item 40159): for the Diag
        # customer areas only, if a VRCID was mentioned (description or comments)
        # this is a formal release, so at least one MFG TE must have been tagged
        # within one day of the closure date. Closing without a VRCID (= no
        # release) or in a non-customer area is left alone.
        if work_item_type == 'Feature' and is_release_customer_area(area_path):
            content_text, mention_events = get_release_signals(work_item_id, wi_fields, auth)
            vrcid_match = VRCID_RE.search(content_text)
            if vrcid_match:
                mfg_guids = get_mfg_te_member_guids(auth)
                close_time = parse_ado_datetime(
                    wi_fields.get('Microsoft.VSTS.Common.ClosedDate')
                    or wi_fields.get('Microsoft.VSTS.Common.StateChangeDate')
                    or wi_fields.get('System.ChangedDate'))
                if mfg_guids and not mfg_tagged_within_window(mention_events, mfg_guids, close_time):
                    reasons.append(f"Release {vrcid_match.group(0)} not notified to MFG. Please "
                                   "@mention at least one MFG TE (QCG/QMF/QMN/QTMC TE) member "
                                   "within one day of closing this Feature.")

        if reasons:
            error_msg = " | ".join(reasons)


            to_mails = {owner_email.lower(), changed_by.lower()}
            cc_mails = {My_Email.lower()}
            if area_path in Area_Manager:
                cc_mails.add(Area_Manager[area_path].lower())
            cc_mails = cc_mails - to_mails

            def build_mention_tags(mails):
                tags = []
                for m in mails:
                    guid, display_name = get_identify_by_email(m, auth)
                    if guid is not None:
                        tag = f'<a href="mailto:{m}" data-vss-mention="version:2.0,guid:{guid}">@{display_name}</a>'
                        tags.append(tag)
                    else:
                        tags.append(f'<a href="mailto:{m}">@{m}</a>')
                return " ".join(tags)
                
            to_mentions_text = build_mention_tags(to_mails)
            cc_mentions_text = build_mention_tags(cc_mails)

            possible_revert_state = ['In Progress', 'Active']
            for revert_state in possible_revert_state:
                revert_body = [
                    {"op": "add", "path": "/fields/System.State", "value": revert_state},
                    {"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to.get('uniqueName', '')},
                    {"op": "add", "path": "/fields/System.History", "value": f"<div>{to_mentions_text}<br>❌ <b>Auto Check Failed</b>: {error_msg}<br>{cc_mentions_text}</div>"}
                ]
                patch_response = requests.patch(wi_url, json=revert_body, auth=auth, headers=headers)
                if patch_response.status_code == 400:
                    logger.info(f"Failed to revert Work Item, status_code:{patch_response.status_code} , WI: {work_item_id} : revert_to: {revert_state} {patch_response.text}")
                    continue                    
                if patch_response.status_code == 200:
                    logger.debug(f"Policy Violated - Work Item Reverted: {work_item_id} to {revert_state}")
                    break
                
            logger.info(f"Revert fail: {work_item_id}, message: {error_msg}")
            return f"Revert fail: {work_item_id}, message: {error_msg}", 200

        logger.debug("Policy Passed")
        return "Policy Passed", 200
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return "Error processing request", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
