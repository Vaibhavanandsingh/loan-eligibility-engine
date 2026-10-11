// Summarise a failed execution from the Error Trigger payload.
//
// The payload has { execution: {id, url, error, lastNodeExecuted}, workflow: {id, name} }.
// When the failure happened inside a trigger node there is no execution yet,
// and the details arrive under `trigger` instead.

const data = $input.first().json;
const execution = data.execution ?? {};
const trigger = data.trigger ?? {};
const workflow = data.workflow ?? {};

const error = execution.error ?? trigger.error ?? {};
const message = String(error.message ?? 'Unknown error').replace(/\s+/g, ' ').slice(0, 1000);
const node = execution.lastNodeExecuted ?? error.node?.name ?? null;
const reason = `Matching failed at step "${node ?? 'unknown'}": ${message}`.slice(0, 500);

const sqlText = (value) => (value == null ? 'NULL' : `'${String(value).replace(/'/g, "''")}'`);

return [{
  json: {
    workflow_name: workflow.name ?? null,
    node,
    message,
    execution_id: execution.id ?? null,
    execution_url: execution.url ?? null,
    q_workflow_id: sqlText(workflow.id),
    q_workflow_name: sqlText(workflow.name),
    q_node: sqlText(node),
    q_message: sqlText(message),
    q_execution_id: sqlText(execution.id),
    q_execution_url: sqlText(execution.url),
    q_reason: sqlText(reason),
  },
}];
