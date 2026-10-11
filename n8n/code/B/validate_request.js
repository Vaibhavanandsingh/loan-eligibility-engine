// Validate the webhook body: { "batch_id": "<uuid>", "valid_rows": <int> }.
// batch_id is later placed into SQL, so anything that is not a UUID is rejected here.
const body = $input.first().json.body ?? {};
const batchId = String(body.batch_id ?? '').trim().toLowerCase();
const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(batchId);

return [{
  json: {
    valid: isUuid,
    batch_id: batchId,
    started_at: Date.now(),
    error: isUuid ? null : 'batch_id must be a UUID',
  },
}];
