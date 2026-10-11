import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { browseDirectory, PathStyle } from "../api/client";
import { Modal } from "./Modal";

interface FolderPickerModalProps {
  pathStyle: PathStyle;
  onSelect: (path: string) => void;
  onClose: () => void;
}

// The host-drive bind mount. On Windows this exposes C:\ (or whatever
// DOCMAN_WINDOWS_HOST_PATH points at); on Linux it is the Compose working dir.
const HOSTFS = "/hostfs";

// Friendly names for the two top-level mounts so the browser reads like the
// host, not container internals.
function rootLabel(name: string): string {
  if (name === HOSTFS) return "Windows / host drive";
  if (name === "/sources") return "Indexed source mounts";
  return name;
}

// Display a container path the way the host would show it: paths under /hostfs
// render as the host drive with backslashes. The real posix path is still what
// gets stored as the scan root — this is presentation only.
function displayPath(p: string | null): string {
  if (p === null) return "This PC";
  if (p === HOSTFS) return "Windows / host drive";
  if (p.startsWith(HOSTFS + "/")) {
    return "Windows / host drive\\" + p.slice(HOSTFS.length + 1).replaceAll("/", "\\");
  }
  return p;
}

export function FolderPickerModal({ pathStyle, onSelect, onClose }: FolderPickerModalProps) {
  // Open straight into the host drive (where a Windows user's folders live)
  // rather than the container mount list. "Up" from here returns to the roots.
  const [currentPath, setCurrentPath] = useState<string | null>(HOSTFS);

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
        <code>{displayPath(currentPath)}</code>
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
                  📁 {currentPath === null ? rootLabel(entry.name) : entry.name}
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
