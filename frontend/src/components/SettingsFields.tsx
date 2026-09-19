import { useState } from "react";
import {
  api,
  type ConfigurationField,
  type ConfigurationSchema,
  type ProviderConnection,
} from "../api/client";
import type { SettingsText } from "../settingsCopy";

type FieldProps = {
  field: ConfigurationField;
  value: unknown;
  onChange: (value: unknown) => void;
  language: string;
  text: SettingsText;
  models: string[];
};
export function SettingField({
  field,
  value,
  onChange,
  language,
  text,
  models,
}: FieldProps) {
  const label = field.label[language] ?? field.label.en;
  const id = `setting-${field.key}`;
  return (
    <div className="configuration-field">
      <label htmlFor={id}>{label}</label>
      {field.kind === "boolean" ? (
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      ) : field.kind === "list" ? (
        field.options?.length ? (
          <div className="configuration-checks">
            {field.options.map((option) => (
              <label key={option}>
                <input
                  type="checkbox"
                  checked={(value as string[]).includes(option)}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...(value as string[]), option]
                        : (value as string[]).filter((item) => item !== option),
                    )
                  }
                />
                {option}
              </label>
            ))}
          </div>
        ) : (
          <StringList
            label={label}
            value={value as string[]}
            onChange={onChange}
            text={text}
          />
        )
      ) : field.kind === "choice" ? (
        <select
          id={id}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value || null)}
        >
          {field.nullable && <option value="">{text.unset}</option>}
          {field.options?.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type={field.kind === "number" ? "number" : "text"}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          step={field.key === "temperature" ? "any" : 1}
          value={value == null ? "" : String(value)}
          placeholder={field.nullable ? text.unset : undefined}
          list={
            field.key.endsWith("think_llm") ? "configuration-models" : undefined
          }
          onChange={(e) =>
            onChange(
              field.kind === "number"
                ? e.target.value === ""
                  ? null
                  : Number(e.target.value)
                : e.target.value,
            )
          }
        />
      )}
      <p className="configuration-help" id={`${id}-help`}>
        {field.description[language] ?? field.description.en}
      </p>
      {field.key === "quick_think_llm" && (
        <datalist id="configuration-models">
          {models.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
      )}
      <details className="configuration-details">
        <summary>
          {text.technical}
        </summary>
        <div>
          <code>{field.key}</code>
        </div>
        <div>
          {text.defaultValue}: <code>{JSON.stringify(field.default)}</code>
        </div>
        {field.env_names?.map((name) => (
          <code key={name}>{name}</code>
        ))}
        {(field.minimum != null || field.maximum != null) && (
          <div>
            {text.range}: {field.minimum ?? "−∞"} … {field.maximum ?? "∞"}
          </div>
        )}
      </details>
    </div>
  );
}

export function StringList({
  label,
  value,
  onChange,
  text,
  options,
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  text: SettingsText;
  options?: string[];
}) {
  const move = (index: number, delta: number) => {
    const next = [...value];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    onChange(next);
  };
  return (
    <div className="configuration-list">
      {value.map((item, index) => (
        <div className="configuration-list-row" key={index}>
          {options ? (
            <select
              aria-label={`${label} ${index + 1}`}
              value={item}
              onChange={(e) =>
                onChange(
                  value.map((old, i) => (i === index ? e.target.value : old)),
                )
              }
            >
              {[...new Set([item, ...options])].map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          ) : (
            <input
              aria-label={`${label} ${index + 1}`}
              value={item}
              onChange={(e) =>
                onChange(
                  value.map((old, i) => (i === index ? e.target.value : old)),
                )
              }
            />
          )}
          <button
            type="button"
            aria-label={`${text.up} ${label} ${index + 1}`}
            disabled={index === 0}
            onClick={() => move(index, -1)}
          >
            ↑
          </button>
          <button
            type="button"
            aria-label={`${text.down} ${label} ${index + 1}`}
            disabled={index === value.length - 1}
            onClick={() => move(index, 1)}
          >
            ↓
          </button>
          <button
            type="button"
            aria-label={`${text.delete} ${label} ${index + 1}`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="button"
        onClick={() =>
          onChange([
            ...value,
            options?.find((option) => !value.includes(option)) ??
              options?.[0] ??
              "",
          ])
        }
      >
        {text.add}
      </button>
    </div>
  );
}

export function RouteEditor({
  name,
  value,
  options,
  onChange,
  text,
}: {
  name: string;
  value: Record<string, string>;
  options: Record<string, string[]>;
  onChange: (v: Record<string, string>) => void;
  text: SettingsText;
}) {
  const [addition, setAddition] = useState("");
  return (
    <div className="configuration-routes">
      {Object.entries(value).map(([category, chain]) => (
        <fieldset key={category}>
          <legend>{category}</legend>
          <StringList
            label={`${name} ${category}`}
            value={chain.split(",")}
            options={["default", ...(options[category] ?? [])]}
            text={text}
            onChange={(items) =>
              onChange({ ...value, [category]: items.join(",") })
            }
          />
          <button
            type="button"
            className="button"
            onClick={() =>
              onChange(
                Object.fromEntries(
                  Object.entries(value).filter(([key]) => key !== category),
                ),
              )
            }
          >
            {text.delete}
          </button>
        </fieldset>
      ))}
      <div className="configuration-list-row">
        <select
          aria-label={`${text.add} ${name}`}
          value={addition}
          onChange={(e) => setAddition(e.target.value)}
        >
          <option value="">{text.select}</option>
          {Object.keys(options)
            .filter((key) => !(key in value))
            .map((key) => (
              <option key={key}>{key}</option>
            ))}
        </select>
        <button
          type="button"
          disabled={!addition}
          onClick={() => {
            onChange({
              ...value,
              [addition]: options[addition][0] ?? "default",
            });
            setAddition("");
          }}
        >
          {text.add}
        </button>
      </div>
    </div>
  );
}

export function CredentialEditor({
  name,
  connectionId,
  configured,
  pending,
  onChange,
  text,
  onError,
}: {
  name: string;
  connectionId?: string;
  configured: boolean;
  pending: string | null | undefined;
  onChange: (value: string | null | undefined) => void;
  text: SettingsText;
  onError: (error: unknown) => void;
}) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  async function reveal(copy = false) {
    try {
      const result = await api.revealCredential(name, connectionId);
      if (copy) {
        await navigator.clipboard.writeText(result.value ?? "");
        setCopied(true);
      } else setRevealed(result.value);
    } catch (error) {
      onError(error);
    }
  }
  return (
    <div className="configuration-credential">
      <label htmlFor={`credential-${name}`}>
        <span>{name === "api_key" ? "API Key" : name}</span> ·{" "}
        {pending === null
          ? text.pendingDelete
          : configured
            ? text.configured
            : text.missing}
      </label>
      <input
        id={`credential-${name}`}
        type="password"
        autoComplete="new-password"
        placeholder={text.replace}
        value={pending ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
      />
      {revealed !== null && (
        <input readOnly aria-label={name} value={revealed} />
      )}
      <div className="configuration-actions">
        <button
          className="button"
          type="button"
          disabled={!configured}
          onClick={() =>
            revealed === null ? void reveal() : setRevealed(null)
          }
        >
          {revealed === null ? text.reveal : text.hide}
        </button>
        <button
          className="button"
          type="button"
          disabled={!configured}
          onClick={() => void reveal(true)}
        >
          {copied ? text.copied : text.copy}
        </button>
        <button
          className="button"
          type="button"
          disabled={!configured && pending === undefined}
          onClick={() => {
            setRevealed(null);
            onChange(null);
          }}
        >
          {text.remove}
        </button>
      </div>
    </div>
  );
}

export function ProviderEditor({
  name,
  label,
  connection,
  onChange,
  schema,
  text,
}: {
  name: string;
  label: string;
  connection: ProviderConnection;
  onChange: (v: ProviderConnection) => void;
  schema: ConfigurationSchema;
  text: SettingsText;
}) {
  const field = (
    key: keyof ProviderConnection,
    title: string,
    hint?: string,
  ) => (
    <label>
      {title}
      <input
        value={String(connection[key] ?? "")}
        placeholder={hint}
        onChange={(e) =>
          onChange({ ...connection, [key]: e.target.value || null })
        }
      />
    </label>
  );
  return (
    <div className="configuration-provider-fields" aria-label={label}>
      {name !== "bedrock" &&
        field(
          "base_url",
          text.address,
          schema.provider_defaults[name]?.base_url ?? text.providerDefault,
        )}
      {name === "azure" && (
        <>
          {field("deployment", text.deploymentName)}
          {field("api_version", text.apiVersion)}
        </>
      )}
      {name === "bedrock" && (
        <>
          {field("region", text.region)}
          {field("aws_profile", text.awsProfile)}
          <label>
            {text.authMode}
            <select
              value={connection.auth_mode ?? "system"}
              onChange={(e) =>
                onChange({
                  ...connection,
                  auth_mode: e.target.value as ProviderConnection["auth_mode"],
                })
              }
            >
              <option value="system">{text.system}</option>
              <option value="bearer">{text.bearer}</option>
              <option value="static">{text.static}</option>
            </select>
          </label>
        </>
      )}
    </div>
  );
}
