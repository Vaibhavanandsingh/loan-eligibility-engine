// Pages that did not go to the LLM: unchanged since the last extraction, failed
// to fetch, or had no usable text. Only status fields are updated; the stored
// hash is left alone, so a failed page is retried on the next run.

const sqlText = (value) => (value == null ? 'NULL' : `'${String(value).replace(/'/g, "''")}'`);

return $input.all().map(({ json: s }) => {
  let status = 'unchanged';
  let error = null;
  if (!s.fetch_ok) {
    status = 'fetch_failed';
    error = s.fetch_error;
  } else if (!s.useful) {
    status = 'no_content';
    error = `only ${s.chars} characters of loan-related text`;
  }
  return {
    json: {
      source_site: s.source_site,
      status,
      q_site: sqlText(s.source_site),
      q_status: sqlText(status),
      q_error: sqlText(error),
    },
  };
});
