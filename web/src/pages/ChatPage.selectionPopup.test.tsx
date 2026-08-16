import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SelectionPopup, isBackwardSelection } from "./ChatPage";

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(undefined),
}));

function selectionFor(
  textNode: Text,
  anchorOffset: number,
  focusOffset: number,
  selectedText: string,
): Selection {
  const range = document.createRange();
  range.setStart(textNode, Math.min(anchorOffset, focusOffset));
  range.setEnd(textNode, Math.max(anchorOffset, focusOffset));
  const rect = {
    x: 24,
    y: 100,
    top: 100,
    right: 224,
    bottom: 120,
    left: 24,
    width: 200,
    height: 20,
    toJSON: () => ({}),
  } as DOMRect;
  Object.defineProperties(range, {
    getClientRects: { value: () => [rect] },
    getBoundingClientRect: { value: () => rect },
  });

  return {
    anchorNode: textNode,
    anchorOffset,
    focusNode: textNode,
    focusOffset,
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
    removeAllRanges: vi.fn(),
    toString: () => selectedText,
  } as unknown as Selection;
}

function Harness({ onReply }: { onReply: (text: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  return (
    <>
      <div ref={containerRef}>
        <p data-testid="transcript-text">alpha beta gamma</p>
      </div>
      <SelectionPopup containerRef={containerRef} onReply={onReply} />
    </>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("transcript selection actions", () => {
  it("recognizes a bottom-to-top selection", () => {
    const textNode = document.createTextNode("alpha beta gamma");
    document.body.append(textNode);
    expect(isBackwardSelection(selectionFor(textNode, 10, 2, "pha beta"))).toBe(true);
    textNode.remove();
  });

  it("stays out of the pointer path until a reverse drag finishes", async () => {
    const onReply = vi.fn();
    render(<Harness onReply={onReply} />);
    const paragraph = screen.getByTestId("transcript-text");
    const textNode = paragraph.firstChild as Text;
    const selection = selectionFor(textNode, 10, 2, "pha beta");
    vi.spyOn(window, "getSelection").mockReturnValue(selection);

    fireEvent.pointerDown(paragraph, { button: 0 });
    fireEvent(document, new Event("selectionchange"));
    expect(screen.queryByRole("toolbar", { name: "Selected text actions" })).toBeNull();

    fireEvent.pointerUp(document, { button: 0 });
    const toolbar = await screen.findByRole("toolbar", { name: "Selected text actions" });
    expect(toolbar).toHaveStyle({ top: "93px", transform: "translate(-50%, -100%)" });
    expect(screen.getByRole("button", { name: "Copy selected text" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Quote selected text in reply" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Quote selected text in reply" }));
    await waitFor(() => expect(onReply).toHaveBeenCalledWith("pha beta"));
    expect(selection.removeAllRanges).toHaveBeenCalledOnce();
  });
});
