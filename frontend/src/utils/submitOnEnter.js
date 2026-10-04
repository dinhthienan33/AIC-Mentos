/** Enter runs the tab action. Shift+Enter keeps a newline. Ignore IME confirm. */
export const submitOnEnter = (event, action) => {
  if (event.key !== 'Enter' || event.shiftKey) return;
  if (event.nativeEvent?.isComposing || event.isComposing) return;
  event.preventDefault();
  action(event);
};
