import { apiClient } from './client'

export type ComparisonMetric = {key:string;label:string;unit:string;reference:number|null;current:number|null;delta:number|null;change_percent:number|null;direction:string}
export type ComparisonRun = {run_id:string;scenario_name:string;environment_name:string;started_at:string|null;finished_at:string;conditions:Record<string,unknown>;evidence:{complete:boolean;reasons:string[]};metrics:Record<string,number|null>}
export type RunComparison = {
  schema_version:number;reference_run_id:string;runs:ComparisonRun[]
  comparisons:Array<{reference_run_id:string;run_id:string;eligible:boolean;reasons:string[];scope:string;differences:Array<{key:string;label:string;reference:unknown;current:unknown;kind:string}>;metrics:ComparisonMetric[]}>
  coverage:{scope:string;selected_run_count:number;observed_step_run_count:number;asset_endpoint_count:number;observed_endpoint_count:number;coverage_ratio:number|null;mapping_complete:boolean;endpoint_details_truncated?:boolean;unmapped_step_run_count:number;historical_endpoint_step_run_count:number;notice:string;endpoints:Array<{endpoint_id:string;method:string;path:string;run_ids:string[];requests:number}>}
  generated_at:string;notice:string
}
export const loadRunComparisonApi = {
  async compare(runIds:string[]):Promise<RunComparison> {
    return (await apiClient.get<{comparison:RunComparison}>(`/api/api-testing/v1/load-run-comparisons?run_ids=${encodeURIComponent(runIds.join(','))}`)).data.comparison
  },
}
