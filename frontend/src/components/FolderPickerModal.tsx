import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { browseDirectory, PathStyle } from "../api/client";
import { Modal } from "./Modal";

interface FolderPickerModalProps {
  pathStyle: PathStyle;
  onSelect: (path: string) => void;
  onClose: () => void;
}

export function FolderPickerModal({ pathStyle, onSelect, onClose }: FolderPickerModalProps) {
  // null = the synthetic top level listing the configured source roots.
  const [currentPath, setCurrentPath] = useState<string | null>(null);

  const browse = useQuery({
    queryKey: ["locations", "browse", currentPath, pathStyle],
    queryFn: () => browseDirectory(currentPath, pathStyle),
  });

  const parent = browse.data?.parent ?? null;
  const canAscend = currentPath !== null;

  function ascend() {
    // parent is null at an allowed root -> step back to the roots list.
    setCurrentPath(parent);
  }

  return (
    <Modal label="Select a source folder" onClose={onClose}>
      <header className="modal-header">
        <button type="button" disabled={!canAscend} onClick={ascend}>
          ↑ Up
        </button>
        <code>{currentPath ?? "Source roots"}</code>
        <button type="button" onClick={onClose}>
          ✕
        </button>
      </header>

      {browse.isLoading && <p>Loading…</p>}
      {browse.isError && <p className="error">{String(browse.error)}</p>}
      {browse.data && browse.data.entries.length === 0 && <p className="empty">No folders here.</p>}
      {browse.data && browse.data.entries.length > 0 && (
        <ul className="folder-picker-list">
          {browse.data.entries.map((entry) => (
            <li key={entry.path}>
              {entry.kind === "dir" ? (
                <button
                  type="button"
                  className="folder-picker-entry dir"
                  onClick={() => setCurrentPath(entry.path)}
                >
                  📁 {entry.name}
                </button>
              ) : (
                <span className="folder-picker-entry file">📄 {entry.name}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <footer className="modal-footer">
        <button
          type="button"
          disabled={currentPath === null}
          onClick={() => {
            if (currentPath !== null) {
              onSelect(currentPath);
              onClose();
            }
          }}
        >
          Select this folder
        </button>
      </footer>
    </Modal>
  );
}
