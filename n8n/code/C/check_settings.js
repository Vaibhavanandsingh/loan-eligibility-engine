// Validate the Settings node before anything is built or sent.
//
// Safety rail: in this assignment real CSV addresses must never be emailed, so
// every recipient must be a plus-alias of the configured test inbox
// (e.g. you+one@gmail.com for you@gmail.com). Anything else stops the run.

const s = $input.first().json;
const batchId = String(s.batch_id ?? '').trim().toLowerCase();
const mode = String(s.mode ?? '').trim();
const inbox = String(s.test_inbox ?? '').trim().toLowerCase();
const recipients = (Array.isArray(s.test_recipients) ? s.test_recipients : []).map((r) => String(r).trim().toLowerCase());
const limit = Number(s.send_limit);

const problems = [];
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(batchId)) problems.push('batch_id must be a UUID');
if (!['dry_run', 'ses'].includes(mode)) problems.push('mode must be dry_run or ses');
if (!Number.isInteger(limit) || limit < 1 || limit > 10) problems.push('send_limit must be 1-10');

const [local, domain] = inbox.split('@');
if (!local || !domain) problems.push('test_inbox must be an email address');
if (recipients.length === 0) problems.push('test_recipients is empty');
for (const r of recipients) {
  const [rl, rd] = r.split('@');
  if (rd !== domain || !rl?.startsWith(`${local}+`)) problems.push(`${r} is not a plus-alias of ${inbox}`);
}
if (!/^[^@\s]+@[^@\s]+$/.test(String(s.sender_email ?? ''))) problems.push('sender_email is not an email address');

if (problems.length) throw new Error(`Workflow C settings invalid: ${problems.join('; ')}`);

return [{
  json: {
    batch_id: batchId,
    mode,
    recipients,
    send_limit: limit,
    sender_email: String(s.sender_email).trim(),
    sender_name: String(s.sender_name ?? 'Loan Eligibility Engine').trim(),
  },
}];
