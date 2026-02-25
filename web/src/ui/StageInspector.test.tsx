import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { StageArtifact } from '../types/city';
import { StageInspector } from './StageInspector';

describe('StageInspector', () => {
  it('renders live generation stage guidance when staged data is not available yet', () => {
    render(
      <StageInspector
        stage={null}
        source="none"
        generationContext={{
          enabled: true,
          progress: {
            status: 'running',
            progress: 0.41,
            phase: 'roads',
            message: 'Routing arterial and collector geometry',
            logs: [
              {
                seq: 1,
                ts: '2026-02-25T12:00:00Z',
                phase: 'roads',
                progress: 0.41,
                message: 'Routing arterial and collector geometry',
              },
            ],
          },
          backendSteps: [
            { id: 'start', title: 'Start', titleZh: '启动', status: 'done', localProgress: 1 },
            { id: 'terrain', title: 'Terrain', titleZh: '地形', status: 'done', localProgress: 1 },
            { id: 'roads', title: 'Roads', titleZh: '道路', status: 'active', localProgress: 0.35 },
            { id: 'artifact', title: 'Artifact', titleZh: '骨架封装', status: 'pending', localProgress: 0 },
          ],
        }}
      />,
    );

    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(screen.getAllByText('主干道').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Arterial Roads').length).toBeGreaterThan(0);
    expect(screen.getByText('Backend Message / 后端进度消息')).toBeInTheDocument();
    expect(screen.getAllByText('Routing arterial and collector geometry').length).toBeGreaterThan(0);
    expect(screen.getByText('Expected Layers / 预计图层')).toBeInTheDocument();
  });

  it('renders staged artifact info in replay mode', () => {
    const stage: StageArtifact = {
      stage_id: 'terrain',
      title: 'Terrain',
      title_zh: '地形',
      subtitle: 'Generating terrain elevation and contour baseline',
      subtitle_zh: '生成地形高程与等高线基底',
      timestamp_ms: 400,
      visible_layers: ['terrain', 'contours'],
      metrics: { foo: 1 },
      layers: {},
      caption: { text: 'Generating terrain elevation and contour baseline', text_zh: '生成地形高程与等高线基底' },
    };

    render(<StageInspector stage={stage} source="staged" generationContext={{ enabled: false, progress: null, backendSteps: [] }} />);

    expect(screen.getByText('地形')).toBeInTheDocument();
    expect(screen.getByText('Terrain')).toBeInTheDocument();
    expect(screen.getByText('contours')).toBeInTheDocument();
    expect(screen.getByText('foo')).toBeInTheDocument();
    expect(screen.queryByText('LIVE')).not.toBeInTheDocument();
  });
});
