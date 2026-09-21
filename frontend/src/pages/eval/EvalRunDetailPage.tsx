/**
 * EvalRunDetailPage — shows a single evaluation run with progress and results.
 */
import { CopyOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Modal,
  Progress,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchHistory } from "@/api/chat";
import { fetchEvalRun } from "@/api/eval";
import type { ThreadHistoryResponse } from "@/types/chat";
import type { EvalCaseResult, EvalRunDetail } from "@/types/eval";

const { Text, Title } = Typography;

export default function EvalRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<EvalCaseResult | null>(null);
  const [history, setHistory] = useState<ThreadHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const load = async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const detail = await fetchEvalRun(runId);
      setRun(detail);
    } catch (err) {
      // Silent fail; keep polling.
    } finally {
      setLoading(false);
    }
  };

  const openProcess = async (record: EvalCaseResult) => {
    setSelected(record);
    setHistory(null);
    if (record.thread_id) {
      setHistoryLoading(true);
      try {
        const h = await fetchHistory(record.thread_id);
        setHistory(h);
      } catch (err) {
        message.error("加载过程记录失败");
      } finally {
        setHistoryLoading(false);
      }
    }
  };

  const closeProcess = () => {
    setSelected(null);
    setHistory(null);
  };

  useEffect(() => {
    void load();
  }, [runId]);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => clearInterval(timer);
  }, [run]);

  if (!run) {
    return (
      <Card size="small">
        <Spin spinning style={{ display: "block", margin: "40px 0" }} />
      </Card>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      {run.error && (
        <Alert message="运行错误" description={run.error} type="error" showIcon />
      )}

      <Card size="small">
        <Space
          style={{ width: "100%", justifyContent: "space-between" }}
          align="center"
        >
          <Title level={5} style={{ margin: 0 }}>
            {run.dataset}
          </Title>
          <Space size="small">
            <Text type="secondary" style={{ fontSize: 12 }}>
              {run.run_id}
            </Text>
            <Tooltip title="复制运行 ID">
              <Button
                size="small"
                type="text"
                icon={<CopyOutlined />}
                onClick={() => {
                  void navigator.clipboard.writeText(run.run_id).then(() => {
                    message.success("运行 ID 已复制");
                  });
                }}
              />
            </Tooltip>
          </Space>
        </Space>
        <Progress
          percent={
            run.total ? Math.round((run.processed / run.total) * 100) : 0
          }
          status={
            run.status === "failed"
              ? "exception"
              : run.status === "completed"
                ? "success"
                : "active"
          }
          showInfo
        />
        <Space wrap>
          <Statistic title="总数" value={run.total} valueStyle={{ fontSize: 14 }} />
          <Statistic title="通过" value={run.passed} valueStyle={{ fontSize: 14 }} />
          <Statistic
            title="失败"
            value={run.processed - run.passed}
            valueStyle={{ fontSize: 14 }}
          />
          <Statistic
            title="准确率"
            value={Math.round((run.metrics.accuracy || 0) * 100)}
            suffix="%"
            valueStyle={{ fontSize: 14 }}
          />
          {run.metrics.tool_call_accuracy !== undefined && (
            <Statistic
              title="工具调用准确率"
              value={Math.round((run.metrics.tool_call_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
          {run.metrics.relevance_accuracy !== undefined && (
            <Statistic
              title="相关性准确率"
              value={Math.round((run.metrics.relevance_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
        </Space>
      </Card>

      <Card title="Case 结果" size="small" loading={loading && run.status === "running"}>
        <Table
          size="small"
          rowKey="result_id"
          pagination={{ pageSize: 10 }}
          dataSource={run.results}
          expandable={{
            expandedRowRender: (record: EvalCaseResult) => (
              <div style={{ margin: 0 }}>
                {record.error && (
                  <Alert
                    message="执行错误"
                    description={record.error}
                    type="error"
                    style={{ marginBottom: 8 }}
                  />
                )}
                {Object.entries(record.scores).map(([name, score]) => (
                  <div key={name} style={{ marginBottom: 8 }}>
                    <Text strong>{name}</Text>
                    <br />
                    <Text type={score.passed ? "success" : "danger"}>
                      {score.passed ? "通过" : "未通过"}: {score.reason}
                    </Text>
                  </div>
                ))}
                {record.gold_sql && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong type="secondary">
                      标准答案 SQL
                    </Text>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        background: "#f6ffed",
                        padding: 8,
                        borderRadius: 4,
                      }}
                    >
                      {record.gold_sql}
                    </pre>
                  </div>
                )}
                {record.generated_sql && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong type="secondary">
                      生成 SQL
                    </Text>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        background: "#e6f4ff",
                        padding: 8,
                        borderRadius: 4,
                      }}
                    >
                      {record.generated_sql}
                    </pre>
                  </div>
                )}
                {!record.gold_sql && !record.generated_sql && record.answer && (
                  <div
                    className="markdown-body"
                    style={{ maxHeight: 240, overflow: "auto" }}
                  >
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {record.answer}
                    </pre>
                  </div>
                )}
              </div>
            ),
          }}
          columns={[
            {
              title: "#",
              dataIndex: "case_index",
              key: "case_index",
              width: 50,
            },
            {
              title: "问题",
              dataIndex: "question",
              key: "question",
              ellipsis: true,
              render: (text: string) => (
                <Tooltip title={text} placement="topLeft">
                  <span>{text}</span>
                </Tooltip>
              ),
            },
            {
              title: "结果",
              dataIndex: "passed",
              key: "passed",
              width: 80,
              render: (passed: boolean) => (
                <Tag color={passed ? "success" : "error"}>
                  {passed ? "通过" : "未通过"}
                </Tag>
              ),
            },
            {
              title: "过程",
              key: "process",
              width: 110,
              render: (_: unknown, record: EvalCaseResult) => (
                <Button size="small" onClick={() => openProcess(record)}>
                  查看过程
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={`Case #${selected?.case_index ?? ""} — 生成过程`}
        open={!!selected}
        onCancel={closeProcess}
        footer={null}
        width={900}
      >
        {selected && (
          <Space direction="vertical" style={{ width: "100%" }}>
            {selected.thread_id && (
              <div>
                <Text strong>Thread ID</Text>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "#f5f5f5",
                    padding: 8,
                    borderRadius: 4,
                    maxHeight: 120,
                    overflow: "auto",
                  }}
                >
                  {selected.thread_id}
                </pre>
              </div>
            )}

            <Spin spinning={historyLoading}>
              {history ? (
                <Space direction="vertical" style={{ width: "100%" }}>
                  {history.messages.map((msg, idx) => (
                    <div key={idx} style={{ width: "100%" }}>
                      <Tag color={msg.role === "user" ? "blue" : msg.role === "assistant" ? "purple" : "default"}>
                        {msg.role === "user" ? "用户" : msg.role === "assistant" ? "模型" : msg.role}
                      </Tag>
                      {msg.content && (
                        <pre
                          style={{
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                            background: msg.role === "user" ? "#e6f4ff" : "#f6ffed",
                            padding: 8,
                            borderRadius: 4,
                            marginTop: 4,
                            maxHeight: 360,
                            overflow: "auto",
                          }}
                        >
                          {msg.content}
                        </pre>
                      )}
                      {msg.tool_calls && msg.tool_calls.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          {msg.tool_calls.map((tc: any, tidx: number) => (
                            <Card
                              key={tidx}
                              size="small"
                              title={`工具调用: ${tc.name || ""}`}
                              style={{ marginBottom: 4, background: "#fffbe6" }}
                            >
                              <div>
                                <Text strong>输入参数：</Text>
                                <pre
                                  style={{
                                    whiteSpace: "pre-wrap",
                                    wordBreak: "break-word",
                                    background: "#fff",
                                    padding: 8,
                                    borderRadius: 4,
                                  }}
                                >
                                  {tc.input ? JSON.stringify(tc.input, null, 2) : ""}
                                </pre>
                              </div>
                              {tc.output && (
                                <div style={{ marginTop: 8 }}>
                                  <Text strong>返回结果：</Text>
                                  <pre
                                    style={{
                                      whiteSpace: "pre-wrap",
                                      wordBreak: "break-word",
                                      background: "#fff",
                                      padding: 8,
                                      borderRadius: 4,
                                      maxHeight: 240,
                                      overflow: "auto",
                                    }}
                                  >
                                    {typeof tc.output === "string" ? tc.output : JSON.stringify(tc.output, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </Card>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </Space>
              ) : selected.thread_id ? (
                <Alert message="正在加载对话过程..." type="info" showIcon />
              ) : (
                <Alert message="该 case 没有保存 thread_id，无法回放生成过程" type="warning" showIcon />
              )}
            </Spin>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
