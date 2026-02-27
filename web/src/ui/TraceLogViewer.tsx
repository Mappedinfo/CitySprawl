import { useCallback, useEffect, useState } from 'react';
import type { TraceLogContent, TraceLogFileInfo, TraceLogListResponse } from '../types/city';
import { deleteTraceLog, fetchTraceLog, fetchTraceLogs } from '../api/client';

type Props = {
  onClose?: () => void;
};

export function TraceLogViewer({ onClose }: Props) {
  const [logs, setLogs] = useState<TraceLogFileInfo[]>([]);
  const [logDir, setLogDir] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<TraceLogContent | null>(null);
  const [selectedFilename, setSelectedFilename] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp: TraceLogListResponse = await fetchTraceLogs();
      setLogs(resp.logs);
      setLogDir(resp.log_dir);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load logs');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  const handleSelectLog = async (filename: string) => {
    if (selectedFilename === filename) {
      setSelectedLog(null);
      setSelectedFilename(null);
      return;
    }
    setLoadingContent(true);
    setError(null);
    try {
      const content = await fetchTraceLog(filename);
      setSelectedLog(content);
      setSelectedFilename(filename);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load log content');
    } finally {
      setLoadingContent(false);
    }
  };

  const handleDeleteLog = async (filename: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete log file "${filename}"?`)) return;
    try {
      await deleteTraceLog(filename);
      if (selectedFilename === filename) {
        setSelectedLog(null);
        setSelectedFilename(null);
      }
      loadLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete log');
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (isoStr: string): string => {
    const d = new Date(isoStr);
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="trace-log-viewer">
      <div className="trace-log-header">
        <span className="trace-log-title">Trace Logs</span>
        <div className="trace-log-actions">
          <button className="trace-log-btn" onClick={loadLogs} disabled={loading}>
            {loading ? '...' : 'Refresh'}
          </button>
          {onClose && (
            <button className="trace-log-btn" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>

      {logDir && <div className="trace-log-dir">{logDir}</div>}

      {error && <div className="trace-log-error">{error}</div>}

      <div className="trace-log-list">
        {logs.length === 0 && !loading && (
          <div className="trace-log-empty">No trace logs found</div>
        )}
        {logs.map((log) => (
          <div
            key={log.filename}
            className={`trace-log-item${selectedFilename === log.filename ? ' selected' : ''}`}
            onClick={() => handleSelectLog(log.filename)}
          >
            <div className="trace-log-item-main">
              <span className="trace-log-filename">{log.filename}</span>
              <span className="trace-log-meta">
                {formatFileSize(log.size_bytes)} | {formatDate(log.modified_at)}
              </span>
            </div>
            <button
              className="trace-log-delete-btn"
              onClick={(e) => handleDeleteLog(log.filename, e)}
              title="Delete"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {loadingContent && <div className="trace-log-loading">Loading...</div>}

      {selectedLog && !loadingContent && (
        <div className="trace-log-content">
          <div className="trace-log-summary">
            <div className="trace-log-summary-title">Summary</div>
            <div className="trace-log-summary-grid">
              <div>
                <span className="label">Total Traces</span>
                <span className="value">{selectedLog.summary?.total_traces ?? 0}</span>
              </div>
              <div>
                <span className="label">Accepted</span>
                <span className="value accepted">{selectedLog.summary?.accepted_traces ?? 0}</span>
              </div>
              <div>
                <span className="label">Rejected</span>
                <span className="value rejected">{selectedLog.summary?.rejected_traces ?? 0}</span>
              </div>
              <div>
                <span className="label">Seed Count</span>
                <span className="value">{selectedLog.seed_count}</span>
              </div>
            </div>

            {selectedLog.summary?.termination_reasons && (
              <div className="trace-log-reasons">
                <div className="reasons-title">Termination Reasons</div>
                <div className="reasons-list">
                  {Object.entries(selectedLog.summary.termination_reasons).map(([reason, count]) => (
                    <span key={reason} className="reason-tag">
                      {reason}: {count as number}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selectedLog.summary?.rejection_reasons && (
              <div className="trace-log-reasons">
                <div className="reasons-title">Rejection Reasons</div>
                <div className="reasons-list">
                  {Object.entries(selectedLog.summary.rejection_reasons).map(([reason, count]) => (
                    <span key={reason} className="reason-tag rejected">
                      {reason}: {count as number}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="trace-log-traces">
            <div className="traces-title">Traces ({selectedLog.traces.length})</div>
            <div className="traces-list">
              {selectedLog.traces.slice(0, 50).map((trace, idx) => (
                <div
                  key={trace.trace_id || idx}
                  className={`trace-item${trace.outcome.accepted ? ' accepted' : ' rejected'}`}
                >
                  <span className="trace-id">{trace.trace_id || `#${idx}`}</span>
                  <span className="trace-status">
                    {trace.outcome.accepted ? '✓' : '✗'}
                  </span>
                  <span className="trace-reason">
                    {trace.outcome.termination_reason || trace.outcome.rejection_reason || '-'}
                  </span>
                  <span className="trace-length">
                    {trace.outcome.total_length.toFixed(0)}m
                  </span>
                  <span className="trace-points">
                    {trace.outcome.point_count} pts
                  </span>
                </div>
              ))}
              {selectedLog.traces.length > 50 && (
                <div className="traces-more">
                  ... and {selectedLog.traces.length - 50} more traces
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
