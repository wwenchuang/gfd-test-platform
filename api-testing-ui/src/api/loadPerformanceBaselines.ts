import {apiClient} from './client'
import type {ComparisonRun,RunComparison} from './loadRunComparison'
export type RegressionPolicy=Partial<Record<'p95_increase_percent'|'rps_decrease_percent'|'http_error_increase_points'|'business_failure_increase_points',number>>
export type PerformanceBaseline={id:string;project_id:string;scenario_id:string;environment_id:string;environment_revision_id:string;source_run_id:string;name:string;adoption_reason:string;status:string;created_at:string;created_by:string;evidence_hash:string;regression_policy:RegressionPolicy;scenario_name:string;environment_name:string;release:string|null;metrics:Record<string,number|null>;evidence_snapshot?:ComparisonRun}
export type BaselineRegression={baseline:PerformanceBaseline;current_run_id:string;state:'inconclusive'|'regression_warning'|'within_reference';message:string;notice:string;checks:Array<{key:string;label:string;unit:string;limit:number;actual:number|null;triggered:boolean|null}>;comparison:RunComparison['comparisons'][number]}
const base='/api/api-testing/v1/load-performance-baselines'
export const performanceBaselinesApi={
 async list(projectId:string):Promise<{baselines:PerformanceBaseline[];truncated:boolean}>{return(await apiClient.get<{baselines:PerformanceBaseline[];truncated:boolean}>(`${base}?project_id=${encodeURIComponent(projectId)}`)).data},
 async adopt(payload:{run_id:string;name:string;adoption_reason:string;regression_policy:RegressionPolicy}):Promise<PerformanceBaseline>{return(await apiClient.post<{baseline:PerformanceBaseline}>(base,payload)).data.baseline},
 async retire(id:string):Promise<PerformanceBaseline>{return(await apiClient.post<{baseline:PerformanceBaseline}>(`${base}/${encodeURIComponent(id)}/retire`,{})).data.baseline},
 async compare(id:string,runId:string):Promise<BaselineRegression>{return(await apiClient.get<{regression:BaselineRegression}>(`${base}/${encodeURIComponent(id)}/compare?run_id=${encodeURIComponent(runId)}`)).data.regression},
}
