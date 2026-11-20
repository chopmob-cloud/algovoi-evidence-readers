// A Go reader for the uncertain-effect/v1 record format.
//
// A second implementation of the fifteen ordered checks the corpus
// corpora/vectors-uncertain-effect states, written to be run under that
// corpus's external-verifier contract:
//
//	<verifier> <case-dir>/record.json --json
//
// prints one JSON object carrying decision and reason, and exits 0 for allow
// and 1 for deny.
//
// It is a port of the Python reader in this repository, not an independent
// reading of the specification: the author wrote both, and the Go code follows
// the same reading of the same text. What it adds is a second language, a
// second code path with its own JSON handling, and a static binary with no
// runtime to install. It is therefore evidence that the checks are
// implementable outside Python, and it is not evidence that the text is
// determinate from a cold start.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"time"
)

const (
	contractID = "https://probityai.github.io/agent-evidence-vectors/predicate/v1/uncertain-effect"
	allow      = "allow"
	deny       = "deny"
	held       = "effect_held"
)

// The reasons in the corpus's order. A reader names the first check that fails.
var firstFailing = []string{
	"contract_id_missing",
	"contract_id_unknown",
	"operation_identity_missing",
	"idempotency_key_args_changed",
	"idempotency_key_follows_trigger",
	"dispatch_beyond_lease",
	"retry_after_unknown",
	"owner_effect_unaccounted",
	"duplicate_effect",
	"unknown_released",
	"outcome_settled_without_authority",
	"outcome_disagrees_with_authority",
	"authority_used_after_revocation",
	"effect_after_stop_reported_quiescent",
	"stop_straddle_reported_settled",
}

// A reply that leaves the caller not knowing whether the effect happened.
var lostReplies = map[string]bool{
	"timeout":         true,
	"connection_lost": true,
	"worker_crashed":  true,
}

// The three events that can set an attempt off.
var triggerKinds = map[string]bool{
	"redelivery": true,
	"replay":     true,
	"completion": true,
}

var reportedOutcomes = map[string]bool{"committed": true, "not_run": true, "unknown": true}

const (
	stopQuiescent      = "quiescent"
	stopNotEstablished = "not_established"
)

// --- JSON access helpers -------------------------------------------------
//
// Every accessor reports whether the value was present and of the expected
// shape, so a check can distinguish "absent" from "null" from "wrong type"
// instead of collapsing them.

func asObject(v any) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	return m, ok
}

func asArray(v any) ([]any, bool) {
	a, ok := v.([]any)
	return a, ok
}

func asString(v any) (string, bool) {
	s, ok := v.(string)
	return s, ok
}

func asInteger(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	i, err := n.Int64()
	if err != nil {
		return 0, false
	}
	return i, true
}

func asTime(v any) (time.Time, bool) {
	s, ok := asString(v)
	if !ok {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

// --- the trail -----------------------------------------------------------

type trail struct {
	record           map[string]any
	operation        map[string]any
	attempts         []map[string]any
	leases           []map[string]any
	answers          []map[string]any
	ownerEffect      any // raw: a count the reader must be able to read as one
	reservation      any
	reported         map[string]any
	stop             map[string]any
	hasStop          bool
	malformedAttempt bool
	hasReported      bool
}

func load(path string) (*trail, error) {
	// The path is this tool's own command-line argument: the corpus harness runs
	// "<verifier> <record.json>", so there is no untrusted source and no privilege
	// boundary to cross. Recorded rather than left silent for the next scan.
	raw, err := os.ReadFile(path) // #nosec G304 G703 -- the path is this tool's own argv

	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var record map[string]any
	if err := dec.Decode(&record); err != nil {
		return nil, err
	}

	t := &trail{record: record}
	if op, ok := asObject(record["operation"]); ok {
		t.operation = op
	}
	if arr, ok := asArray(record["attempts"]); ok {
		for _, item := range arr {
			if m, ok := asObject(item); ok {
				t.attempts = append(t.attempts, m)
			} else {
				// An entry the reader cannot read is not an attempt that did not
				// happen. Dropping it would let the rest of the trail be judged as
				// if the slot were empty, so the record fails the identity check.
				t.malformedAttempt = true
			}
		}
	}
	// Dispatch order is what several checks read, and the corpus does not
	// promise the array is sorted.
	sort.SliceStable(t.attempts, func(i, j int) bool {
		ti, _ := asTime(t.attempts[i]["dispatchedAt"])
		tj, _ := asTime(t.attempts[j]["dispatchedAt"])
		return ti.Before(tj)
	})
	if arr, ok := asArray(record["leases"]); ok {
		for _, item := range arr {
			if m, ok := asObject(item); ok {
				t.leases = append(t.leases, m)
			}
		}
	}
	if arr, ok := asArray(record["reconciliations"]); ok {
		for _, item := range arr {
			if m, ok := asObject(item); ok {
				t.answers = append(t.answers, m)
			}
		}
	}
	t.ownerEffect = record["ownerEffects"]
	t.reservation = record["reservation"]
	if rep, ok := asObject(record["reported"]); ok {
		t.reported = rep
		t.hasReported = true
	}
	if stop, ok := asObject(record["stop"]); ok {
		t.stop = stop
		t.hasStop = true
	}
	return t, nil
}

func (t *trail) opKey() string {
	s, _ := asString(t.operation["idempotencyKey"])
	return s
}

func (t *trail) opDigest() string {
	s, _ := asString(t.operation["argsDigest"])
	return s
}

func (t *trail) lost(attempt map[string]any) bool {
	reply, _ := asString(attempt["reply"])
	return lostReplies[reply]
}

// answerAboutThisEffect reports whether an owner answer speaks about this
// operation. An answer carrying another key is not a statement about this
// effect, so it settles nothing and covers nothing.
func (t *trail) answerAboutThisEffect(answer map[string]any) bool {
	key, ok := asString(answer["idempotencyKey"])
	return ok && key != "" && key == t.opKey()
}

// settled is the outcome the effect owner's own answers settle.
func (t *trail) settled() string {
	for _, attempt := range t.attempts {
		if reply, _ := asString(attempt["reply"]); reply == "confirmed" {
			return "committed"
		}
	}
	for _, answer := range t.answers {
		if !t.answerAboutThisEffect(answer) {
			continue
		}
		if result, _ := asString(answer["result"]); result == "present" {
			return "committed"
		}
	}
	var lastLost time.Time
	sawLost := false
	for _, attempt := range t.attempts {
		if !t.lost(attempt) {
			continue
		}
		at, ok := asTime(attempt["dispatchedAt"])
		if !ok {
			continue
		}
		if !sawLost || at.After(lastLost) {
			lastLost = at
			sawLost = true
		}
	}
	if !sawLost {
		return "not_run"
	}
	for _, answer := range t.answers {
		if !t.answerAboutThisEffect(answer) {
			continue
		}
		result, _ := asString(answer["result"])
		final, _ := answer["final"].(bool)
		at, ok := asTime(answer["at"])
		if result == "absent" && final && ok && !at.Before(lastLost) {
			return "not_run"
		}
	}
	return "unknown"
}

func (t *trail) absentAnswerBetween(start, end time.Time) bool {
	for _, answer := range t.answers {
		if !t.answerAboutThisEffect(answer) {
			continue
		}
		result, _ := asString(answer["result"])
		final, _ := answer["final"].(bool)
		at, ok := asTime(answer["at"])
		if result == "absent" && final && ok && !at.Before(start) && !at.After(end) {
			return true
		}
	}
	return false
}

// --- the fifteen checks --------------------------------------------------

func contractMissing(t *trail) bool {
	_, present := t.record["contractId"]
	return !present
}

func contractUnknown(t *trail) bool {
	id, _ := asString(t.record["contractId"])
	return id != contractID
}

func identityIncomplete(t *trail) bool {
	if t.malformedAttempt || t.opKey() == "" || t.opDigest() == "" {
		return true
	}
	for _, attempt := range t.attempts {
		key, _ := asString(attempt["idempotencyKey"])
		digest, _ := asString(attempt["argsDigest"])
		if key == "" || digest == "" {
			return true
		}
	}
	return false
}

func keyReusedWithOtherArguments(t *trail) bool {
	for _, attempt := range t.attempts {
		key, _ := asString(attempt["idempotencyKey"])
		digest, _ := asString(attempt["argsDigest"])
		if key == t.opKey() && digest != t.opDigest() {
			return true
		}
	}
	return false
}

// keyFollowsTrigger refuses an attempt that a redelivery, replay or completion
// set off but that carries a key of its own, and refuses a trigger the reader
// cannot read: an unverifiable claim is not a satisfied one.
func keyFollowsTrigger(t *trail) bool {
	for _, attempt := range t.attempts {
		raw, present := attempt["trigger"]
		if !present || raw == nil {
			continue
		}
		trigger, ok := asObject(raw)
		if !ok {
			return true
		}
		kind, ok := asString(trigger["kind"])
		if !ok || !triggerKinds[kind] {
			return true
		}
		key, _ := asString(attempt["idempotencyKey"])
		if key != t.opKey() {
			return true
		}
	}
	return false
}

func insideLease(t *trail, attempt map[string]any) bool {
	sent, ok := asTime(attempt["dispatchedAt"])
	if !ok {
		return false
	}
	worker, _ := asString(attempt["worker"])
	token, hasToken := asInteger(attempt["fencingToken"])
	if !hasToken {
		return false
	}
	var lease map[string]any
	found := false
	for _, candidate := range t.leases {
		cw, _ := asString(candidate["worker"])
		ct, ok := asInteger(candidate["fencingToken"])
		if ok && cw == worker && ct == token {
			lease = candidate
			found = true
			break
		}
	}
	if !found {
		return false
	}
	acquired, okA := asTime(lease["acquiredAt"])
	expires, okE := asTime(lease["expiresAt"])
	if !okA || !okE || sent.Before(acquired) || !sent.Before(expires) {
		return false
	}
	// A lease with a higher token granted before the send takes the right to
	// dispatch away from this holder, even inside its own window.
	for _, other := range t.leases {
		otherToken, ok := asInteger(other["fencingToken"])
		if !ok || otherToken <= token {
			continue
		}
		otherAcquired, ok := asTime(other["acquiredAt"])
		if ok && !otherAcquired.After(sent) {
			return false
		}
	}
	return true
}

func dispatchedBeyondLease(t *trail) bool {
	if len(t.leases) == 0 {
		for _, attempt := range t.attempts {
			if attempt["worker"] != nil || attempt["fencingToken"] != nil {
				return true
			}
		}
		return false
	}
	for _, attempt := range t.attempts {
		if !insideLease(t, attempt) {
			return true
		}
	}
	return false
}

func retriedAfterLostReply(t *trail) bool {
	owner, _ := asObject(t.record["effectOwner"])
	deduplicates, _ := owner["deduplicatesByKey"].(bool)
	retention, hasRetention := asInteger(owner["keyRetentionSeconds"])

	for i, earlier := range t.attempts {
		if !t.lost(earlier) {
			continue
		}
		sent, ok := asTime(earlier["dispatchedAt"])
		if !ok {
			continue
		}
		earlierKey, _ := asString(earlier["idempotencyKey"])
		for _, later := range t.attempts[i+1:] {
			resent, ok := asTime(later["dispatchedAt"])
			if !ok {
				continue
			}
			laterKey, _ := asString(later["idempotencyKey"])
			within := true
			if hasRetention {
				within = resent.Sub(sent) <= time.Duration(retention)*time.Second
			}
			if deduplicates && laterKey == earlierKey && within {
				continue
			}
			if t.absentAnswerBetween(sent, resent) {
				continue
			}
			return true
		}
	}
	return false
}

// ownerEffectUnaccounted compares the owner's ledger with the attempts that
// could have produced an effect. A count the reader cannot read is left to the
// duplicate check, which names it.
func ownerEffectUnaccounted(t *trail) bool {
	count, ok := asInteger(t.ownerEffect)
	if !ok || count < 0 {
		return false
	}
	var could int64
	for _, attempt := range t.attempts {
		reply, _ := asString(attempt["reply"])
		if reply != "no_effect" {
			could++
		}
	}
	return count > could
}

func moreThanOneEffect(t *trail) bool {
	count, ok := asInteger(t.ownerEffect)
	if !ok {
		return true
	}
	if count < 0 {
		return true
	}
	return count > 1
}

func chargeDroppedWhileUnknown(t *trail) bool {
	reservation, ok := asObject(t.reservation)
	if !ok {
		return false
	}
	charged, _ := reservation["charged"].(bool)
	return t.settled() == "unknown" && !charged
}

func unknownReportedAsSettled(t *trail) bool {
	outcome, _ := asString(t.reported["outcome"])
	return t.settled() == "unknown" && (outcome == "committed" || outcome == "not_run")
}

func reportedOutcomeDisagrees(t *trail) bool {
	outcome, ok := asString(t.reported["outcome"])
	if !ok || !reportedOutcomes[outcome] {
		return true
	}
	settled := t.settled()
	return settled != "unknown" && outcome != settled
}

// authorityUsedAfterRevocation reads the stop block: a check made at or after
// the revocation that was not refused is authority the stop had withdrawn.
func authorityUsedAfterRevocation(t *trail) bool {
	if !t.hasStop {
		return false
	}
	revoked, ok := asTime(t.stop["revokedAt"])
	if !ok {
		return false
	}
	checks, _ := asArray(t.stop["authorityChecks"])
	for _, item := range checks {
		check, ok := asObject(item)
		if !ok {
			return true
		}
		at, ok := asTime(check["at"])
		if !ok {
			return true
		}
		if at.Before(revoked) {
			continue
		}
		result, _ := asString(check["result"])
		if result != "refused" {
			return true
		}
	}
	return false
}

func commitInterval(t *trail) (time.Time, time.Time, bool) {
	if !t.hasStop {
		return time.Time{}, time.Time{}, false
	}
	commit, ok := asObject(t.stop["ownerCommit"])
	if !ok {
		return time.Time{}, time.Time{}, false
	}
	notBefore, okA := asTime(commit["notBefore"])
	notAfter, okB := asTime(commit["notAfter"])
	if !okA || !okB {
		return time.Time{}, time.Time{}, false
	}
	return notBefore, notAfter, true
}

func acknowledgedAt(t *trail) (time.Time, bool) {
	if !t.hasStop {
		return time.Time{}, false
	}
	return asTime(t.stop["acknowledgedAt"])
}

func effectAfterStopReportedQuiescent(t *trail) bool {
	notBefore, _, ok := commitInterval(t)
	if !ok {
		return false
	}
	ack, ok := acknowledgedAt(t)
	if !ok {
		return false
	}
	reported, _ := asString(t.stop["reported"])
	return notBefore.After(ack) && reported == stopQuiescent
}

func stopStraddleReportedSettled(t *trail) bool {
	notBefore, notAfter, ok := commitInterval(t)
	if !ok {
		return false
	}
	ack, ok := acknowledgedAt(t)
	if !ok {
		return false
	}
	if ack.Before(notBefore) || ack.After(notAfter) {
		return false
	}
	reported, _ := asString(t.stop["reported"])
	return reported != stopNotEstablished
}

var checks = map[string]func(*trail) bool{
	"contract_id_missing":                  contractMissing,
	"contract_id_unknown":                  contractUnknown,
	"operation_identity_missing":           identityIncomplete,
	"idempotency_key_args_changed":         keyReusedWithOtherArguments,
	"idempotency_key_follows_trigger":      keyFollowsTrigger,
	"dispatch_beyond_lease":                dispatchedBeyondLease,
	"retry_after_unknown":                  retriedAfterLostReply,
	"owner_effect_unaccounted":             ownerEffectUnaccounted,
	"duplicate_effect":                     moreThanOneEffect,
	"unknown_released":                     chargeDroppedWhileUnknown,
	"outcome_settled_without_authority":    unknownReportedAsSettled,
	"outcome_disagrees_with_authority":     reportedOutcomeDisagrees,
	"authority_used_after_revocation":      authorityUsedAfterRevocation,
	"effect_after_stop_reported_quiescent": effectAfterStopReportedQuiescent,
	"stop_straddle_reported_settled":       stopStraddleReportedSettled,
}

func judge(t *trail) (string, string) {
	for _, reason := range firstFailing {
		check, ok := checks[reason]
		if !ok {
			// A reason with no predicate would silently pass its cases.
			return deny, reason
		}
		if check(t) {
			return deny, reason
		}
	}
	return allow, held
}

func main() {
	var recordPath string
	for _, arg := range os.Args[1:] {
		if arg == "--json" || arg == "--native" {
			continue
		}
		if recordPath == "" {
			recordPath = arg
		}
	}
	if recordPath == "" {
		fmt.Fprintln(os.Stderr, "usage: algovoi-uncertain-effect-go <record.json> [--json]")
		os.Exit(2)
	}

	t, err := load(recordPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot read %s: %v\n", recordPath, err)
		os.Exit(2)
	}

	decision, reason := judge(t)
	out, _ := json.Marshal(map[string]string{"decision": decision, "reason": reason})
	fmt.Println(string(out))
	if decision == allow {
		os.Exit(0)
	}
	os.Exit(1)
}
