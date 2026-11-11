import type { BoardData, ViewState } from "../derive/types";

/** The props of every section view below the header. */
export type SectionProps = {
  readonly data: BoardData;
  readonly state: ViewState;
  /** The 1 way a section changes the view state; the app writes the hash (spec 0006 section 11.5). */
  readonly setState?: (next: ViewState) => void;
};
