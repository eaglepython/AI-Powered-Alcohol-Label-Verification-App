import { useState, useCallback } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_KEY = import.meta.env.VITE_API_KEY || "";
const authHeaders = () => (API_KEY ? { "X-API-Key": API_KEY } : {});

const STATUS_CONFIG = {
  APPROVED: { color: "#16a34a", bg: "#f0fdf4", border: "#86efac", icon: "✅", label: "APPROVED" },
  REJECTED: { color: "#dc2626", bg: "#fef2f2", border: "#fca5a5", icon: "❌", label: "REJECTED" },
  NEEDS_REVIEW: { color: "#d97706", bg: "#fffbeb", border: "#fcd34d", icon: "⚠️", label: "NEEDS REVIEW" },
};

function FieldRow({ name, field }) {
  if (!field) return null;
  const label = name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 12,
      padding: "10px 0", borderBottom: "1px solid #f1f5f9"
    }}>
      <span style={{ fontSize: 16, flexShrink: 0, marginTop: 2 }}>
        {field.compliant ? "✅" : field.found ? "⚠️" : "❌"}
      </span>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "#374151", marginBottom: 2 }}>{label}</div>
        {field.value
          ? <div style={{ fontSize: 13, color: "#6b7280", wordBreak: "break-word" }}>{field.value}</div>
          : <div style={{ fontSize: 13, color: "#ef4444", fontStyle: "italic" }}>Not found</div>
        }
        {field.issue && (
          <div style={{ fontSize: 12, color: "#dc2626", marginTop: 4, background: "#fef2f2", padding: "4px 8px", borderRadius: 4 }}>
            {field.issue}
          </div>
        )}
      </div>
    </div>
  );
}

function ResultCard({ result, index }) {
  const [expanded, setExpanded] = useState(index === 0);
  const cfg = STATUS_CONFIG[result.overall_status] || STATUS_CONFIG.NEEDS_REVIEW;

  return (
    <div style={{
      border: `2px solid ${cfg.border}`, borderRadius: 12,
      background: cfg.bg, marginBottom: 16, overflow: "hidden"
    }}>
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "14px 18px", cursor: "pointer"
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 20 }}>{cfg.icon}</span>
          <div>
            <div style={{ fontWeight: 700, color: cfg.color, fontSize: 15 }}>{cfg.label}</div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>
              {result.label_id} · {result.processing_time_ms}ms ·
              Confidence: {Math.round((result.confidence || 0.85) * 100)}%
            </div>
          </div>
        </div>
        <span style={{ color: "#9ca3af", fontSize: 18 }}>{expanded ? "▲" : "▼"}</span>
      </div>

      {expanded && (
        <div style={{ padding: "0 18px 18px", borderTop: `1px solid ${cfg.border}` }}>

          {/* Issues */}
          {result.issues?.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontWeight: 600, color: "#374151", marginBottom: 8, fontSize: 13 }}>
                🚨 Compliance Issues ({result.issues.length})
              </div>
              {result.issues.map((issue, i) => (
                <div key={i} style={{
                  background: "#fef2f2", border: "1px solid #fca5a5",
                  borderRadius: 6, padding: "8px 12px", marginBottom: 6,
                  fontSize: 13, color: "#dc2626"
                }}>
                  {issue}
                </div>
              ))}
            </div>
          )}

          {/* Fields */}
          {result.fields && Object.keys(result.fields).length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontWeight: 600, color: "#374151", marginBottom: 8, fontSize: 13 }}>
                📋 Extracted Fields
              </div>
              <div style={{ background: "white", borderRadius: 8, padding: "0 12px", border: "1px solid #e5e7eb" }}>
                {Object.entries(result.fields).map(([name, field]) => (
                  <FieldRow key={name} name={name} field={field} />
                ))}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {result.recommendations?.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontWeight: 600, color: "#374151", marginBottom: 8, fontSize: 13 }}>
                💡 Recommendations
              </div>
              {result.recommendations.map((rec, i) => (
                <div key={i} style={{
                  background: "#eff6ff", border: "1px solid #bfdbfe",
                  borderRadius: 6, padding: "8px 12px", marginBottom: 6,
                  fontSize: 13, color: "#1d4ed8"
                }}>
                  {rec}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState("single"); // single | batch
  const [dragOver, setDragOver] = useState(false);
  const [batchSummary, setBatchSummary] = useState(null);

  const handleFiles = useCallback((newFiles) => {
    const imageFiles = Array.from(newFiles).filter(f => f.type.startsWith("image/"));
    if (imageFiles.length === 0) {
      setError("Please upload image files only (JPEG, PNG, WebP)");
      return;
    }
    setFiles(imageFiles);
    setResults([]);
    setError(null);
    setBatchSummary(null);
    setMode(imageFiles.length > 1 ? "batch" : "single");
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const handleVerify = async () => {
    if (files.length === 0) return;
    setLoading(true);
    setError(null);
    setResults([]);
    setBatchSummary(null);

    try {
      if (files.length === 1) {
        const formData = new FormData();
        formData.append("file", files[0]);
        const res = await fetch(`${API_URL}/verify`, { method: "POST", body: formData, headers: authHeaders() });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Verification failed");
        }
        const data = await res.json();
        setResults([data]);
      } else {
        const formData = new FormData();
        files.forEach(f => formData.append("files", f));
        const res = await fetch(`${API_URL}/verify/batch`, { method: "POST", body: formData, headers: authHeaders() });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Batch verification failed");
        }
        const data = await res.json();
        setResults(data.results);
        setBatchSummary({
          total: data.total,
          approved: data.approved,
          rejected: data.rejected,
          needs_review: data.needs_review,
          total_ms: data.total_processing_time_ms
        });
      }
    } catch (e) {
      setError(e.message || "An unexpected error occurred");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc", fontFamily: "system-ui, -apple-system, sans-serif" }}>

      {/* HEADER */}
      <div style={{ background: "#003366", padding: "16px 24px", display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ fontSize: 28 }}>🏛️</div>
        <div>
          <div style={{ color: "white", fontWeight: 700, fontSize: 18, lineHeight: 1.2 }}>
            TTB Label Verification System
          </div>
          <div style={{ color: "#93c5fd", fontSize: 13 }}>
            Department of the Treasury · Alcohol and Tobacco Tax and Trade Bureau
          </div>
        </div>
        <div style={{ marginLeft: "auto", background: "#16a34a", color: "white", borderRadius: 20, padding: "4px 12px", fontSize: 12, fontWeight: 600 }}>
          AI-POWERED
        </div>
      </div>

      <div style={{ maxWidth: 860, margin: "0 auto", padding: "32px 20px" }}>

        {/* UPLOAD ZONE */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById("file-input").click()}
          style={{
            border: `2px dashed ${dragOver ? "#003366" : "#cbd5e1"}`,
            borderRadius: 16,
            background: dragOver ? "#eff6ff" : "white",
            padding: "40px 24px",
            textAlign: "center",
            cursor: "pointer",
            marginBottom: 20,
            transition: "all 0.2s"
          }}
        >
          <div style={{ fontSize: 40, marginBottom: 12 }}>📤</div>
          <div style={{ fontWeight: 600, color: "#1e293b", fontSize: 16, marginBottom: 6 }}>
            Upload Label Image{files.length > 1 ? "s" : ""}
          </div>
          <div style={{ color: "#64748b", fontSize: 14, marginBottom: 8 }}>
            Drag and drop or click to browse · JPEG, PNG, WebP
          </div>
          <div style={{ color: "#94a3b8", fontSize: 12 }}>
            Single label or batch upload (up to 50 labels)
          </div>
          <input
            id="file-input" type="file" accept="image/*" multiple
            style={{ display: "none" }}
            onChange={(e) => handleFiles(e.target.files)}
          />
        </div>

        {/* SELECTED FILES */}
        {files.length > 0 && (
          <div style={{ background: "white", borderRadius: 10, border: "1px solid #e2e8f0", padding: "12px 16px", marginBottom: 16 }}>
            <div style={{ fontWeight: 600, color: "#374151", marginBottom: 8, fontSize: 13 }}>
              {files.length} file{files.length > 1 ? "s" : ""} selected
              {files.length > 1 && <span style={{ color: "#7c3aed", marginLeft: 8, fontWeight: 700 }}>BATCH MODE</span>}
            </div>
            {files.slice(0, 5).map((f, i) => (
              <div key={i} style={{ fontSize: 12, color: "#6b7280", padding: "2px 0" }}>
                📄 {f.name} ({(f.size / 1024).toFixed(1)} KB)
              </div>
            ))}
            {files.length > 5 && (
              <div style={{ fontSize: 12, color: "#9ca3af" }}>...and {files.length - 5} more</div>
            )}
          </div>
        )}

        {/* VERIFY BUTTON */}
        <button
          onClick={handleVerify}
          disabled={files.length === 0 || loading}
          style={{
            width: "100%", padding: "14px",
            background: files.length === 0 || loading ? "#94a3b8" : "#003366",
            color: "white", border: "none", borderRadius: 10,
            fontSize: 15, fontWeight: 700, cursor: files.length === 0 || loading ? "not-allowed" : "pointer",
            marginBottom: 24, transition: "background 0.2s",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 10
          }}
        >
          {loading ? (
            <>
              <span style={{ animation: "spin 1s linear infinite", display: "inline-block" }}>⟳</span>
              Analyzing label{files.length > 1 ? "s" : ""}...
            </>
          ) : (
            <>🔍 Verify {files.length > 1 ? `${files.length} Labels (Batch)` : "Label"}</>
          )}
        </button>

        {/* ERROR */}
        {error && (
          <div style={{
            background: "#fef2f2", border: "1px solid #fca5a5",
            borderRadius: 10, padding: "14px 18px", marginBottom: 20,
            color: "#dc2626", fontSize: 14
          }}>
            ⚠️ {error}
          </div>
        )}

        {/* BATCH SUMMARY */}
        {batchSummary && (
          <div style={{
            background: "#f8fafc", border: "1px solid #e2e8f0",
            borderRadius: 12, padding: "16px 20px", marginBottom: 20
          }}>
            <div style={{ fontWeight: 700, color: "#1e293b", marginBottom: 12, fontSize: 15 }}>
              Batch Results — {batchSummary.total} labels in {batchSummary.total_ms}ms
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {[
                { label: "Approved", count: batchSummary.approved, color: "#16a34a", bg: "#f0fdf4" },
                { label: "Rejected", count: batchSummary.rejected, color: "#dc2626", bg: "#fef2f2" },
                { label: "Needs Review", count: batchSummary.needs_review, color: "#d97706", bg: "#fffbeb" },
              ].map(s => (
                <div key={s.label} style={{
                  background: s.bg, border: `1px solid ${s.color}30`,
                  borderRadius: 8, padding: "10px 16px", textAlign: "center", flex: 1, minWidth: 100
                }}>
                  <div style={{ fontWeight: 700, fontSize: 22, color: s.color }}>{s.count}</div>
                  <div style={{ fontSize: 12, color: s.color }}>{s.label}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* RESULTS */}
        {results.length > 0 && (
          <div>
            <div style={{ fontWeight: 700, color: "#1e293b", marginBottom: 14, fontSize: 16 }}>
              Verification Results
            </div>
            {results.map((result, i) => (
              <ResultCard key={i} result={result} index={i} />
            ))}
          </div>
        )}

        {/* FOOTER */}
        <div style={{ textAlign: "center", color: "#94a3b8", fontSize: 12, marginTop: 40, padding: "20px 0", borderTop: "1px solid #e2e8f0" }}>
          TTB Label Verification System · Department of the Treasury · Built by Joseph Bidias
          <br />AI-powered compliance analysis · Results require agent review before final determination
        </div>
      </div>

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
