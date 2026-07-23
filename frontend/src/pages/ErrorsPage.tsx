import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchErrors, reindexDocument } from "../api/client";

export function ErrorsPage() {
  const queryClient = useQueryClient();
  const errors = useQuery({
    queryKey: ["errors"],
    queryFn: fetchErrors,
    refetchInterval: 5_000,
  });

  const reindex = useMutation({
    mutationFn: reindexDocument,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["errors"] });
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  return (
    <section>
      <h2>Errors</h2>
      <p className="notice">Documents whose latest indexing attempt failed. Errors are isolated per document.</p>
      {reindex.isError && <p className="error">Reindex failed: {String(reindex.error)}</p>}
      {reindex.isSuccess && <p className="notice">Reindex queued.</p>}
      {errors.isLoading && <p>Loading errors…</p>}
      {errors.isError && <p className="error">Unable to load errors: {String(errors.error)}</p>}
      {errors.data?.data.length === 0 && <p className="empty">No document errors. 🎉</p>}
      {errors.data && errors.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Name</th>
              <th>Path</th>
              <th>Error</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {errors.data.data.map((doc) => (
              <tr key={doc.id}>
                <td>{doc.file_name}</td>
                <td>
                  <code>{doc.display_path}</code>
                </td>
                <td className="error">
                  <strong>{doc.error?.code}</strong>
                  {doc.error?.message ? ` — ${doc.error.message}` : ""}
                </td>
                <td>
                  <button
                    disabled={reindex.isPending && reindex.variables === doc.id}
                    onClick={() => reindex.mutate(doc.id)}
                  >
                    {reindex.isPending && reindex.variables === doc.id ? "Queuing…" : "Retry"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
