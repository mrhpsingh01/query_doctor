/** Plain-language descriptions of the PostgreSQL plan operators QueryDoctor meets most often. */
export const OPERATOR_HELP: Record<string, string> = {
  "Seq Scan": "Reads every row of the table, in order, and applies the filter to each one. Fast for small tables; costly when most rows are thrown away.",
  "Index Scan": "Walks an index to find matching rows, then fetches each from the table. Efficient when few rows match.",
  "Index Only Scan": "Answers the query from the index alone, without visiting the table, when the index contains every needed column.",
  "Bitmap Index Scan": "Collects the locations of matching rows from an index into a bitmap, to be fetched in table order.",
  "Bitmap Heap Scan": "Fetches the table rows that a bitmap index scan pointed to, reading each table page once.",
  "Nested Loop": "For each row from the outer input, looks up matching rows in the inner input. Great with an index on the inner side; slow when the inner side is scanned repeatedly.",
  "Hash Join": "Builds an in-memory hash table from one input and probes it with the other. Good for large, unsorted inputs.",
  "Merge Join": "Joins two inputs that are both sorted on the join key by walking them side by side.",
  Hash: "Builds the hash table that a Hash Join probes.",
  Sort: "Orders rows by the sort key. Spills to temporary files when the rows do not fit in work_mem.",
  "Incremental Sort": "Sorts rows in batches when they already arrive partly ordered.",
  Aggregate: "Combines rows into groups or a single total (count, sum, max, …).",
  HashAggregate: "Groups rows by hashing the grouping key.",
  GroupAggregate: "Groups rows that arrive already sorted by the grouping key.",
  Limit: "Stops after the requested number of rows.",
  Gather: "Collects the rows produced by parallel worker processes.",
  "Gather Merge": "Collects sorted rows from parallel workers and keeps them in order.",
  Materialize: "Stores its input in memory so it can be read more than once.",
  "CTE Scan": "Reads the result of a WITH query.",
  "Subquery Scan": "Reads the result of a subquery.",
  Result: "Computes a value without reading a table.",
};

/** MongoDB query-plan stages (find) and aggregation stages (aggregate). */
export const MONGO_STAGE_HELP: Record<string, string> = {
  COLLSCAN: "Reads every document in the collection and tests the filter on each one. Fast for a small collection; costly when most documents are thrown away.",
  IXSCAN: "Walks an index to find the keys that match, without reading the documents themselves yet.",
  FETCH: "Reads the full documents that the index scan pointed to, and applies any filter the index could not.",
  IDHACK: "Looks one document up by its _id.",
  SORT: "Orders documents in memory (or on disk when they do not fit). It must see every document before it can return the first.",
  SORT_KEY_GENERATOR: "Computes the value each document is sorted by.",
  PROJECTION_SIMPLE: "Keeps only the requested fields of each document.",
  PROJECTION_COVERED: "Builds the result from the index alone, without reading documents.",
  PROJECTION_DEFAULT: "Keeps only the requested fields of each document.",
  LIMIT: "Stops after the requested number of documents.",
  SKIP: "Discards the first documents of the result.",
  EOF: "The query can return nothing, so no data is read.",
  $cursor: "The query part of an aggregation that MongoDB pushes down to its find engine.",
  $match: "Keeps only the documents that satisfy a filter. Early in a pipeline it can use an index.",
  $group: "Groups documents and combines each group (count, sum, …). It reads every incoming document.",
  $sort: "Orders documents. Directly after a $match on the same fields, an index can supply the order.",
  $lookup: "Joins each document with matching documents of another collection. Without an index on the joined field, every document triggers a scan.",
  $project: "Reshapes each document, keeping or computing fields.",
  $unwind: "Emits one document per element of an array field.",
  $limit: "Stops after the requested number of documents.",
  $skip: "Discards the first documents of the result.",
  $count: "Counts the incoming documents.",
};

export const NO_OPERATOR_HELP = "No built-in description for this operator yet. See the PostgreSQL documentation on EXPLAIN for details.";

export function operatorHelp(nodeType: string): string {
  return OPERATOR_HELP[nodeType] ?? MONGO_STAGE_HELP[nodeType] ?? NO_OPERATOR_HELP;
}
