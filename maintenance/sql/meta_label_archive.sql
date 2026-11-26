-- Add a distinct coefficient type; do not label logistic coefficients as ICs.
ALTER TABLE public.ml_params DROP CONSTRAINT ml_params_kind_check;
ALTER TABLE public.ml_params ADD CONSTRAINT ml_params_kind_check
 CHECK (kind IN ('signed_ic','importance','coefficient'));
