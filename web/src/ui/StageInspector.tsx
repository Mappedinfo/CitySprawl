import { UNIFIED_STAGE_DEFS, canonicalizePhaseId, type CanonicalStageId } from '../timeline/unifiedStages';
import type { GenerateJobLog, StageArtifact } from '../types/city';

type StageSource = 'v2' | 'staged' | 'fallback' | 'json' | 'none';

type StageInspectorStepState = {
  id: string;
  title: string;
  titleZh: string;
  status: 'pending' | 'active' | 'done';
  localProgress: number;
};

type StageInspectorGenerationContext = {
  enabled: boolean;
  progress: {
    status: string;
    progress: number;
    phase: string;
    message: string;
    logs?: GenerateJobLog[];
  } | null;
  backendSteps: StageInspectorStepState[];
};

type StageInspectorProps = {
  stage: StageArtifact | null;
  source: StageSource;
  generationContext?: StageInspectorGenerationContext;
};

const STAGE_DEF_BY_ID = new Map(UNIFIED_STAGE_DEFS.map((def) => [def.id, def] as const));

const STAGE_GUIDE_BLURBS: Record<CanonicalStageId, { zh: string; en: string }> = {
  start: {
    zh: '建立任务上下文、初始化渲染与后端生成通道。',
    en: 'Initialize generation state, renderer context and backend execution channel.',
  },
  terrain: {
    zh: '生成高度场、坡度与等高线基础数据，建立后续约束。',
    en: 'Build terrain elevation/slope and contour baseline for downstream constraints.',
  },
  rivers: {
    zh: '确定河道中心线与河流区域，影响道路、地块与洪涝分析。',
    en: 'Select river centerlines/areas that constrain roads, blocks and flood analysis.',
  },
  hubs: {
    zh: '放置城市中心点与层级节点，作为道路与命名的核心锚点。',
    en: 'Place hub hierarchy points that anchor roads, labels and traffic demand.',
  },
  roads: {
    zh: '生成主骨架与层级道路网络，并逐步补齐连接关系。',
    en: 'Generate arterial/collector/local hierarchy and refine network connectivity.',
  },
  artifact: {
    zh: '封装核心城市骨架与预览产物，为后续分析阶段提供输入。',
    en: 'Package core city artifact and previews for downstream analysis stages.',
  },
  analysis: {
    zh: '计算宜居性、洪涝风险与资源分布等分析图层。',
    en: 'Compute suitability, flood risk and resource distribution analysis layers.',
  },
  traffic: {
    zh: '进行交通分配与拥堵热度预估，叠加在道路网络上。',
    en: 'Assign traffic demand and estimate congestion over the generated road network.',
  },
  buildings: {
    zh: '生成建筑轮廓与绿地预览，用于形态与密度感知。',
    en: 'Generate building footprints and green-zone previews for morphology cues.',
  },
  parcels: {
    zh: '提取街区、宗地和步行路径，形成土地细分结构。',
    en: 'Extract blocks, parcels and pedestrian paths to form land subdivision structure.',
  },
  stages: {
    zh: '组装阶段快照与展示时间线，用于回放与对比查看。',
    en: 'Assemble stage snapshots and timeline playback composites.',
  },
  done: {
    zh: '城市生成完成，输出最终可视化与阶段回放数据。',
    en: 'Generation complete with final city artifact and replay-ready stages.',
  },
};

function formatPercent(v: number): string {
  const n = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
  return `${Math.round(n * 100)}%`;
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0));
}

function resolveGenerationStage(context: StageInspectorGenerationContext | undefined) {
  if (!context?.enabled) return null;
  const progress = context.progress;
  const phaseLike = String(progress?.phase || progress?.status || '');
  const canonical = canonicalizePhaseId(phaseLike) as CanonicalStageId;
  const activeStep = context.backendSteps.find((s) => s.status === 'active')
    ?? [...context.backendSteps].reverse().find((s) => s.status === 'done')
    ?? context.backendSteps[0]
    ?? null;
  const fallbackCanonical = activeStep ? (canonicalizePhaseId(activeStep.id) as CanonicalStageId) : 'start';
  const stageId = (STAGE_DEF_BY_ID.has(canonical) ? canonical : fallbackCanonical) as CanonicalStageId;
  const def = STAGE_DEF_BY_ID.get(stageId) ?? STAGE_DEF_BY_ID.get('start')!;
  return { def, activeStep, canonical: stageId };
}

function GenerationStagePreview({ context }: { context: StageInspectorGenerationContext }) {
  const resolved = resolveGenerationStage(context);
  const progress = context.progress;
  if (!resolved) return <p className="muted">No generation preview available.</p>;
  const { def, activeStep, canonical } = resolved;
  const guide = STAGE_GUIDE_BLURBS[canonical] ?? STAGE_GUIDE_BLURBS.start;
  const logs = [...(progress?.logs ?? [])].slice(-4).reverse();
  const statusText = String(progress?.status ?? 'running');
  const message = String(progress?.message ?? '').trim();
  const phaseText = String(progress?.phase || statusText || def.id);
  const percentText = formatPercent(Number(progress?.progress ?? 0));

  return (
    <>
      <div className="stage-live-head">
        <span className="stage-live-badge">LIVE</span>
        <span className={`progress-status status-${statusText.toLowerCase()}`}>{statusText}</span>
        <span className="stage-live-percent">{percentText}</span>
      </div>

      <div className="stage-name-stack">
        <div className="stage-name-zh">{def.titleZh}</div>
        <div className="stage-name-en">{def.title}</div>
      </div>

      <div className="stage-subcopy">{def.subtitleZh}</div>
      <div className="stage-subcopy stage-subcopy-en">{def.subtitle}</div>

      <div className="metrics-list stage-runtime-meta">
        <div>
          <span>phase</span>
          <strong>{phaseText}</strong>
        </div>
        <div>
          <span>active step</span>
          <strong>{activeStep ? `${activeStep.titleZh} / ${activeStep.title}` : def.id}</strong>
        </div>
        <div>
          <span>progress</span>
          <strong>{percentText}</strong>
        </div>
      </div>

      {message ? (
        <div className="stage-message-card">
          <div className="stage-message-title">Backend Message / 后端进度消息</div>
          <div className="stage-message-body">{message}</div>
        </div>
      ) : null}

      <div className="stage-guide-card">
        <div className="stage-guide-title">Step Guide / 步骤说明</div>
        <div className="stage-guide-body">{guide.zh}</div>
        <div className="stage-guide-body stage-guide-body-en">{guide.en}</div>
      </div>

      <div className="stage-section-title">Expected Layers / 预计图层</div>
      <div className="stage-visible-list">
        {def.visibleLayers.map((item) => (
          <span key={item} className="stage-token">
            {item}
          </span>
        ))}
      </div>

      <div className="stage-section-title">Backend Steps / 后端步骤</div>
      <div className="stage-backend-step-list">
        {context.backendSteps.map((step) => (
          <div key={step.id} className={`stage-backend-step stage-backend-step-${step.status}`}>
            <div className="stage-backend-step-head">
              <div className="stage-backend-step-labels">
                <span className="stage-backend-step-zh">{step.titleZh}</span>
                <span className="stage-backend-step-en">{step.title}</span>
              </div>
              <span className={`stage-backend-step-status status-${step.status}`}>{step.status}</span>
            </div>
            <div className="stage-backend-step-track" aria-hidden="true">
              <span className="stage-backend-step-fill" style={{ width: `${Math.round(clamp01(step.localProgress) * 100)}%` }} />
            </div>
          </div>
        ))}
      </div>

      {logs.length ? (
        <>
          <div className="stage-section-title">Recent Logs / 最近日志</div>
          <div className="stage-log-list">
            {logs.map((log) => (
              <div key={`${log.seq}:${log.ts}`} className="stage-log-item">
                <div className="stage-log-topline">
                  <span className="stage-log-phase">{canonicalizePhaseId(log.phase || '') || log.phase || 'phase'}</span>
                  <span className="stage-log-progress">{formatPercent(Number(log.progress ?? 0))}</span>
                </div>
                <div className="stage-log-message">{log.message || '(no message)'}</div>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

export function StageInspector({ stage, source, generationContext }: StageInspectorProps) {
  const showGenerationPreview = Boolean(generationContext?.enabled);

  return (
    <aside className="hud-panel stage-inspector">
      <div className="hud-title-row">
        <h2>Stage</h2>
        <span className={`source-pill source-${source}`}>{source}</span>
      </div>
      {showGenerationPreview ? (
        <GenerationStagePreview context={generationContext!} />
      ) : !stage ? (
        <p className="muted">No staged data.</p>
      ) : (
        <>
          <div className="stage-name-stack">
            <div className="stage-name-zh">{stage.title_zh}</div>
            <div className="stage-name-en">{stage.title}</div>
          </div>
          <div className="stage-subcopy">{stage.subtitle_zh}</div>
          <div className="stage-subcopy stage-subcopy-en">{stage.subtitle}</div>
          <div className="stage-visible-list">
            {stage.visible_layers.map((item) => (
              <span key={item} className="stage-token">
                {item}
              </span>
            ))}
          </div>
          <div className="metrics-list stage-metrics-list">
            {Object.entries(stage.metrics).map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <strong>{String(v)}</strong>
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

