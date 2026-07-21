import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createLocation,
  deleteLocation,
  fetchLocationCapabilities,
  fetchLocations,
  pickFolderNative,
  requestLocationScan,
  PathStyle,
} from "../api/client";
import { FolderPickerModal } from "../components/FolderPickerModal";

export function detectPathStyle(scanRoot: string): PathStyle {
  if (/^\\\\/.test(scanRoot)) return "unc";
  if (/^[A-Za-z]:[\\/]/.test(scanRoot)) return "mapped_drive";
  return "linux";
}

const PATH_STYLE_LABELS: Record<PathStyle, string> = {
  linux: "Linux",
  windows: "Windows (local drive)",
  unc: "UNC share",
  mapped_drive: "Mapped drive",
};

export function LocationsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [scanRoot, setScanRoot] = useState("");
  const [pathStyle, setPathStyle] = useState<PathStyle | "auto">("auto");
  const [pickerOpen, setPickerOpen] = useState(false);
  const locations = useQuery({ queryKey: ["locations"], queryFn: fetchLocations });
  const capabilities = useQuery({
    queryKey: ["locations", "capabilities"],
    queryFn: fetchLocationCapabilities,
  });

  function applyPickedPath(path: string) {
    setScanRoot(path);
    setPathStyle("auto"); // let detectPathStyle re-derive from the picked path
  }

  const nativePick = useMutation({
    mutationFn: pickFolderNative,
    onSuccess: (picked) => {
      // A cancelled native dialog returns null — leave the field untouched.
      if (picked.path) applyPickedPath(picked.path);
    },
  });

  function openFolderPicker() {
    // Native OS dialog when the host supports it (Windows / WSL interop);
    // otherwise the in-app directory browser. Auto-selected, no user choice.
    if (capabilities.data?.native_picker_available) {
      nativePick.mutate();
    } else {
      setPickerOpen(true);
    }
  }
  const create = useMutation({
    mutationFn: createLocation,
    onSuccess: async () => {
      setName("");
      setScanRoot("");
      setPathStyle("auto");
      await queryClient.invalidateQueries({ queryKey: ["locations"] });
    },
  });
  const scan = useMutation({ mutationFn: requestLocationScan });
  const remove = useMutation({
    mutationFn: deleteLocation,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["locations"] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({
      name,
      scan_root: scanRoot,
      path_style: pathStyle === "auto" ? detectPathStyle(scanRoot) : pathStyle,
    });
  }

  return (
    <section>
      <h2>Locations</h2>
      {capabilities.data && (
        <p className="notice">
          Filesystem profile: <strong>{capabilities.data.filesystem_profile}</strong>
          {capabilities.data.native_picker_available
            ? " — Browse opens the native folder dialog."
            : " — Browse opens the in-app directory browser."}
        </p>
      )}
      <form className="create-form" onSubmit={submit}>
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} required />
        </label>
        <label>
          Scan root
          <span className="input-with-button">
            <input
              value={scanRoot}
              onChange={(event) => setScanRoot(event.target.value)}
              placeholder="Z:\Documents or /sources/documents"
              required
            />
            <button type="button" disabled={nativePick.isPending} onClick={openFolderPicker}>
              {nativePick.isPending ? "Waiting for Explorer…" : "Browse…"}
            </button>
          </span>
        </label>
        <label>
          Path style
          <select
            value={pathStyle}
            onChange={(event) => setPathStyle(event.target.value as PathStyle | "auto")}
          >
            <option value="auto">Auto ({PATH_STYLE_LABELS[detectPathStyle(scanRoot)]})</option>
            {(Object.keys(PATH_STYLE_LABELS) as PathStyle[]).map((style) => (
              <option key={style} value={style}>
                {PATH_STYLE_LABELS[style]}
              </option>
            ))}
          </select>
        </label>
        <button disabled={create.isPending} type="submit">
          {create.isPending ? "Creating…" : "Add location"}
        </button>
      </form>
      {create.isError && <p className="error">Could not create location: {String(create.error)}</p>}
      {remove.isError && <p className="error">Could not delete location: {String(remove.error)}</p>}
      {nativePick.isError && (
        <p className="error">Folder picker failed: {String(nativePick.error)}</p>
      )}
      {scan.isSuccess && <p className="notice">Scan queued.</p>}
      {locations.isLoading && <p>Loading locations…</p>}
      {locations.isError && (
        <p className="error">Unable to load locations: {String(locations.error)}</p>
      )}
      {locations.data?.data.length === 0 && <p className="empty">No locations configured.</p>}
      {locations.data && locations.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Name</th>
              <th>Root</th>
              <th>State</th>
              <th>Last scan</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {locations.data.data.map((location) => (
              <tr key={location.id}>
                <td>{location.name}</td>
                <td>
                  <code>{location.display_root}</code>
                </td>
                <td>{location.enabled ? "enabled" : "disabled"}</td>
                <td>
                  {location.last_successful_scan_at
                    ? new Date(location.last_successful_scan_at).toLocaleString()
                    : "never"}
                </td>
                <td>
                  <button disabled={scan.isPending} onClick={() => scan.mutate(location.id)}>
                    Scan
                  </button>
                  <button
                    className="danger"
                    disabled={remove.isPending}
                    onClick={() => {
                      if (window.confirm(`Delete location "${location.name}"?`)) {
                        remove.mutate({ id: location.id, revision: location.revision });
                      }
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pickerOpen && (
        <FolderPickerModal
          pathStyle={pathStyle === "auto" ? detectPathStyle(scanRoot) : pathStyle}
          onSelect={applyPickedPath}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </section>
  );
}
